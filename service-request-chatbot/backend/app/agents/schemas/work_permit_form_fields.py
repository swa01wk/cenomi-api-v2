"""Tenant Work Permit form field maps — copied from portal forms JSON.

Source: ``cenomi.workflow_service_request_mapping.forms`` where
``category = 'WORK_PERMIT'`` (tenant-visible fields only; admin TenantSelect
omitted). Checked in so the chatbot does not query the platform DB at runtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final


@dataclass(frozen=True)
class WpTenantFormSpec:
    """Per-subtype tenant create form shape."""

    # Keys copied into POST payload.payload (forms.intialState.common).
    common_keys: tuple[str, ...]
    # Field name → portal widget type.
    field_types: dict[str, str]
    # User-facing required keys once work_permit_type is known (tenant path).
    # Nested widgets listed by top-level name; emptiness rules live in helpers.
    required_tenant_fields: tuple[str, ...]
    # Optional tenant keys (empty string / empty skip is OK).
    optional_tenant_fields: frozenset[str]


# Backend / lease lookup still required for every create.
CREATE_LEASE_REQUIRED: Final[tuple[str, ...]] = (
    "lease_code",
    "lease_id",
    "property_id",
    "unit_codes",
    "mall",
    "brand",
    "work_permit_type",
)

_COLD_COMMON: Final[tuple[str, ...]] = (
    "mall",
    "brand",
    "lease",
    "notes",
    "owner",
    "phone",
    "workers",
    "type_work",
    "unit_type",
    "work_info",
    "department",
    "trade_name",
    "unit_codes",
    "planneddate",
    "company_name",
    "country_code",
    "list_equipment",
    "tenant_contact",
    "lease_brand_mall",
    "responsible_person",
)

_HOT_COMMON: Final[tuple[str, ...]] = (
    "mall",
    "brand",
    "lease",
    "phone",
    "title",
    "workers",
    "unit_type",
    "work_type",
    "unit_codes",
    "description",
    "planneddate",
    "company_name",
    "country_code",
    "work_location",
    "tenant_contact",
    "contractor_name",
    "measureofsafety",
    "lease_brand_mall",
    "demand_appendices",
    "special_conditions",
)

_MAINT_HOT_COMMON: Final[tuple[str, ...]] = (
    "mall",
    "brand",
    "lease",
    "phone",
    "workers",
    "unit_type",
    "work_type",
    "department",
    "unit_codes",
    "planneddate",
    "company_name",
    "country_code",
    "work_location",
    "tenant_contact",
    "contractor_name",
    "measureofsafety",
    "lease_brand_mall",
    "demand_appendices",
    "special_conditions",
)

_OPS_COMMON: Final[tuple[str, ...]] = (
    "mall",
    "brand",
    "lease",
    "notes",
    "phone",
    "unit_type",
    "department",
    "entry_gate",
    "typeofwork",
    "unit_codes",
    "firstperiod",
    "company_name",
    "secondperiod",
    "tenant_contact",
    "lease_brand_mall",
    "effective_starting",
    "permission_given_to",
)

_COLD_TYPES: Final[dict[str, str]] = {
    "lease_brand_mall": "LeaseBrandMall",
    "department": "dropdown",
    "unit_type": "input",
    "owner": "input",
    "trade_name": "input",
    "responsible_person": "input",
    "phone": "phoneinput",
    "type_work": "textarea",
    "list_equipment": "textarea",
    "work_info": "textarea",
    "notes": "textarea",
    "planneddate": "planneddate",
    "workers": "listWorker",
}

_HOT_TYPES: Final[dict[str, str]] = {
    "lease_brand_mall": "LeaseBrandMall",
    "unit_type": "input",
    "contractor_name": "input",
    "phone": "phoneinput",
    "work_type": "textarea",
    "work_location": "textarea",
    "workers": "addworker",
    "planneddate": "planneddate",
    "measureofsafety": "measureofsafety",
    "demand_appendices": "textarea",
    "special_conditions": "textarea",
}

_MAINT_HOT_TYPES: Final[dict[str, str]] = {
    **_HOT_TYPES,
    "department": "dropdown",
}

_OPS_TYPES: Final[dict[str, str]] = {
    "lease_brand_mall": "LeaseBrandMall",
    "department": "dropdown",
    "unit_type": "input",
    "phone": "phoneinput",
    "typeofwork": "typeofwork",
    "effective_starting": "daterange_picker",
    "firstperiod": "timepicker",
    "secondperiod": "timepicker",
    "entry_gate": "input",
    "notes": "textarea",
}

_COLD_REQUIRED: Final[tuple[str, ...]] = (
    "department",
    "unit_type",
    "owner",
    "trade_name",
    "responsible_person",
    "phone",
    "type_work",
    "list_equipment",
    "work_info",
    "planneddate",
    "workers",
)

_HOT_REQUIRED: Final[tuple[str, ...]] = (
    "unit_type",
    "contractor_name",
    "phone",
    "planneddate",
    "workers",
    "measureofsafety",
)

_ROOF_REQUIRED: Final[tuple[str, ...]] = (
    "unit_type",
    "contractor_name",
    "phone",
    "work_type",
    "work_location",
    "demand_appendices",
    "special_conditions",
    "planneddate",
    "workers",
    "measureofsafety",
)

_MAINT_HOT_REQUIRED: Final[tuple[str, ...]] = (
    "department",
    "unit_type",
    "contractor_name",
    "phone",
    "planneddate",
    "workers",
    "measureofsafety",
)

_OPS_REQUIRED: Final[tuple[str, ...]] = (
    "department",
    "unit_type",
    "phone",
    "typeofwork",
    "permission_given_to",
    "effective_starting",
    "firstperiod",
)

TENANT_FORM_BY_SUBTYPE: Final[dict[str, WpTenantFormSpec]] = {
    "CONSTRUCTION_COLD": WpTenantFormSpec(
        common_keys=_COLD_COMMON,
        field_types=_COLD_TYPES,
        required_tenant_fields=_COLD_REQUIRED,
        optional_tenant_fields=frozenset({"notes"}),
    ),
    "MAINTENANCE_COLD": WpTenantFormSpec(
        common_keys=_COLD_COMMON,
        field_types=_COLD_TYPES,
        required_tenant_fields=_COLD_REQUIRED,
        optional_tenant_fields=frozenset({"notes"}),
    ),
    "CONSTRUCTION_HOT": WpTenantFormSpec(
        common_keys=_HOT_COMMON,
        field_types=_HOT_TYPES,
        required_tenant_fields=_HOT_REQUIRED,
        optional_tenant_fields=frozenset(
            {"work_type", "work_location", "demand_appendices", "special_conditions", "description", "title"}
        ),
    ),
    "CONSTRUCTION_ROOF_ACCESS": WpTenantFormSpec(
        common_keys=tuple(k for k in _HOT_COMMON if k not in {"title", "description"}),
        field_types=_HOT_TYPES,
        required_tenant_fields=_ROOF_REQUIRED,
        optional_tenant_fields=frozenset(),
    ),
    "MAINTENANCE_HOT": WpTenantFormSpec(
        common_keys=_MAINT_HOT_COMMON,
        field_types=_MAINT_HOT_TYPES,
        required_tenant_fields=_MAINT_HOT_REQUIRED,
        optional_tenant_fields=frozenset(
            {"work_type", "work_location", "demand_appendices", "special_conditions"}
        ),
    ),
    "MAINTENANCE_ROOF_ACCESS": WpTenantFormSpec(
        common_keys=_MAINT_HOT_COMMON,
        field_types=_MAINT_HOT_TYPES,
        required_tenant_fields=_MAINT_HOT_REQUIRED,
        optional_tenant_fields=frozenset(
            {"work_type", "work_location", "demand_appendices", "special_conditions"}
        ),
    ),
    "OPERATIONS": WpTenantFormSpec(
        common_keys=_OPS_COMMON,
        field_types=_OPS_TYPES,
        required_tenant_fields=_OPS_REQUIRED,
        optional_tenant_fields=frozenset({"secondperiod", "entry_gate", "notes"}),
    ),
}

FIELD_LABELS: Final[dict[str, str]] = {
    "lease_code": "Lease Code",
    "lease_brand_mall": "Lease / Brand / Mall",
    "work_permit_type": "Work Permit Type",
    "department": "Department",
    "unit_type": "Unit Type",
    "owner": "Owner",
    "trade_name": "Trade Name",
    "responsible_person": "Responsible Person",
    "phone": "Phone",
    "type_work": "Type of Work",
    "list_equipment": "List of Equipment",
    "work_info": "Work Info",
    "notes": "Notes",
    "contractor_name": "Contractor Name",
    "work_type": "Work Type",
    "work_location": "Work Location",
    "demand_appendices": "Demand Appendices",
    "special_conditions": "Special Conditions",
    "planneddate": "Planned Date and Time",
    "workers": "Workers",
    "measureofsafety": "Measure of Safety",
    "typeofwork": "Type of Work",
    "permission_given_to": "Permission Given To",
    "effective_starting": "Effective Starting Dates",
    "firstperiod": "First Period",
    "secondperiod": "Second Period",
    "entry_gate": "Entry Gate",
    "description": "Description",
    "title": "Title",
}


def form_spec_for(subtype: str | None) -> WpTenantFormSpec | None:
    token = str(subtype or "").upper()
    return TENANT_FORM_BY_SUBTYPE.get(token)


def _planneddate_incomplete(value: Any) -> bool:
    if not isinstance(value, dict):
        return True
    return not str(value.get("worktimedate") or "").strip() or not str(
        value.get("completionworkdate") or ""
    ).strip()


def _workers_incomplete(value: Any, widget: str) -> bool:
    if widget == "listWorker":
        if not isinstance(value, list) or not value:
            return True
        row = value[0] if isinstance(value[0], dict) else {}
        return not str(row.get("full_name") or "").strip()
    # addworker
    if not isinstance(value, dict):
        return True
    workers = value.get("worker")
    if not isinstance(workers, list) or not workers:
        return True
    row = workers[0] if isinstance(workers[0], dict) else {}
    return not str(row.get("name") or "").strip()


def _measureofsafety_incomplete(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return True
    return not any(str(v or "").strip() for v in value.values())


def _typeofwork_incomplete(value: Any) -> bool:
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not any(str(v or "").strip() for v in value)
    if not isinstance(value, dict):
        return True
    inner = value.get("value")
    if isinstance(inner, list):
        return not any(str(v or "").strip() for v in inner)
    return not str(inner or "").strip()


def _normalize_typeofwork_token(raw: str) -> str | None:
    """Map a label or key to a portal checkbox key, or None if unknown."""
    token = str(raw or "").strip().lower()
    if not token:
        return None
    # Collapse punctuation / extra spaces so "Inventory/Shop Arrangement" matches.
    collapsed = " ".join(
        token.replace("/", " ").replace("-", " ").replace("_", " ").split()
    )
    if collapsed in _TYPEOFWORK_KEYS:
        return collapsed
    if collapsed in _TYPEOFWORK_LABEL_TO_KEY:
        return _TYPEOFWORK_LABEL_TO_KEY[collapsed]
    # Compact form without spaces (shopfitting already covered; inventoryshop…).
    compact = collapsed.replace(" ", "")
    if compact in _TYPEOFWORK_KEYS:
        return compact
    if compact in _TYPEOFWORK_LABEL_TO_KEY:
        return _TYPEOFWORK_LABEL_TO_KEY[compact]
    return None


def _coerce_typeofwork_value(parsed: Any) -> dict[str, Any]:
    """Normalise typeofwork to portal shape ``{value: [keys…], otheroption}``."""
    other = ""
    tokens: list[str] = []

    if isinstance(parsed, str):
        tokens = [parsed] if parsed.strip() else []
    elif isinstance(parsed, list):
        tokens = [str(t) for t in parsed if t is not None and str(t).strip()]
    elif isinstance(parsed, dict):
        other = str(parsed.get("otheroption") or "").strip()
        raw_val = parsed.get("value")
        if isinstance(raw_val, list):
            tokens = [str(t) for t in raw_val if t is not None and str(t).strip()]
        elif raw_val is not None and str(raw_val).strip():
            tokens = [str(raw_val)]
    else:
        return dict(EMPTY_TYPEOFWORK)

    keys: list[str] = []
    unknown: list[str] = []
    for token in tokens:
        mapped = _normalize_typeofwork_token(token)
        if mapped:
            if mapped not in keys:
                keys.append(mapped)
        else:
            unknown.append(token.strip())

    if unknown:
        if "others" not in keys:
            keys.append("others")
        # Prefer an explicit otheroption; else join unrecognised labels.
        if not other:
            other = ", ".join(unknown)

    if not keys and other:
        keys = ["others"]

    return {"value": keys, "otheroption": other}


def _effective_starting_incomplete(value: Any) -> bool:
    if not isinstance(value, dict):
        return True
    return not str(value.get("datefrom") or "").strip() or not str(
        value.get("dateto") or ""
    ).strip()


def _period_incomplete(value: Any) -> bool:
    if not isinstance(value, dict):
        return True
    return not str(value.get("from") or "").strip() or not str(value.get("to") or "").strip()


def is_tenant_field_missing(
    field_name: str,
    value: Any,
    *,
    field_type: str | None = None,
    optional: bool = False,
) -> bool:
    """True when *value* does not satisfy a create-time required check."""
    if optional:
        return value is None
    widget = field_type or ""
    if field_name == "planneddate" or widget == "planneddate":
        return _planneddate_incomplete(value)
    if field_name == "workers" or widget in {"listWorker", "addworker"}:
        return _workers_incomplete(value, widget or "listWorker")
    if field_name == "measureofsafety" or widget == "measureofsafety":
        return _measureofsafety_incomplete(value)
    if field_name == "typeofwork" or widget == "typeofwork":
        return _typeofwork_incomplete(value)
    if field_name == "effective_starting" or widget == "daterange_picker":
        return _effective_starting_incomplete(value)
    if field_name in {"firstperiod", "secondperiod"} or widget == "timepicker":
        return _period_incomplete(value)
    return value is None or value == "" or value == []


EMPTY_PLANNEDDATE: Final[dict[str, str]] = {
    "worktimeto": "",
    "completionto": "",
    "worktimedate": "",
    "worktimefrom": "",
    "completionfrom": "",
    "completionworkdate": "",
}

EMPTY_ADDWORKER: Final[dict[str, Any]] = {
    "worker": [
        {
            "name": "",
            "designation": "",
            "document_id": "",
            "iqama_number": "",
            "worker_qualification": "",
        }
    ],
    "instructor_name": "",
}

EMPTY_LIST_WORKER: Final[list[dict[str, str]]] = [
    {
        "phone": "",
        "full_name": "",
        "designation": "",
        "company_name": "",
        "country_code": "966",
    }
]

EMPTY_MEASURE_OF_SAFETY: Final[dict[str, str]] = {
    "fire_hoses": "",
    "sprinker_on": "",
    "volatile_remove": "",
    "fire_extinguisher": "",
    "fire_proofcoating": "",
    "work_hour_organized": "",
    "equipment_for_carryingout": "",
    "all_Holes_in_floorandWalls": "",
}

EMPTY_TYPEOFWORK: Final[dict[str, Any]] = {"value": [], "otheroption": ""}
EMPTY_EFFECTIVE_STARTING: Final[dict[str, str]] = {"dateto": "", "datefrom": ""}
EMPTY_PERIOD: Final[dict[str, str]] = {"to": "", "from": ""}

# Portal TypeOfWork checkbox keys (must match typeOfWork.tsx option.value).
_TYPEOFWORK_KEYS: Final[frozenset[str]] = frozenset(
    {
        "construction",
        "shopfitting",
        "stockdelivery",
        "stockremoval",
        "inventory",
        "maintenance",
        "others",
    }
)

# Human labels / common typos → portal checkbox key.
_TYPEOFWORK_LABEL_TO_KEY: Final[dict[str, str]] = {
    "construction": "construction",
    "shop fitting": "shopfitting",
    "shopfitting": "shopfitting",
    "stock delivery": "stockdelivery",
    "stockdelivery": "stockdelivery",
    "stock removal": "stockremoval",
    "stockremoval": "stockremoval",
    "inventory": "inventory",
    "inventory/shop arrangement": "inventory",
    "inventory / shop arrangement": "inventory",
    "inventory shop arrangement": "inventory",
    "inventoryshoparrangment": "inventory",
    "maintenance": "maintenance",
    "maintenance inside the shop": "maintenance",
    "maintance inside the shop": "maintenance",  # portal i18n typo
    "maintenanace inside the shop": "maintenance",  # common misspelling
    "others": "others",
    "other": "others",
}

_NESTED_WIDGETS: Final[frozenset[str]] = frozenset(
    {
        "planneddate",
        "listWorker",
        "addworker",
        "measureofsafety",
        "typeofwork",
        "daterange_picker",
        "timepicker",
    }
)


def _to_ddmmyyyy(value: Any) -> str:
    """Normalise a date string to DD/MM/YYYY.

    Handles:
    - DD/MM/YYYY  (pass-through)
    - YYYY-MM-DD  (ISO → DD/MM/YYYY)
    - DD-MM-YYYY  (dashes, 4-digit year → DD/MM/YYYY)
    - DD-MM-YY    (dashes, 2-digit year → DD/MM/20YY)
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "/" in raw[:10]:
        return raw[:10]
    date_part = raw[:10]
    parts = date_part.split("-")
    if len(parts) == 3:
        a, b, c = parts
        if len(a) == 4:
            # YYYY-MM-DD → DD/MM/YYYY
            return f"{c}/{b}/{a}"
        if len(c) == 4:
            # DD-MM-YYYY → DD/MM/YYYY
            return f"{a}/{b}/{c}"
        if len(c) == 2:
            # DD-MM-YY → DD/MM/20YY
            return f"{a}/{b}/20{c}"
    return raw


def empty_value_for(field_name: str, field_type: str | None = None) -> Any:
    """Return the portal empty shape for a nested widget, else ``\"\"``."""
    widget = field_type or ""
    if field_name == "planneddate" or widget == "planneddate":
        return dict(EMPTY_PLANNEDDATE)
    if field_name == "workers" or widget == "listWorker":
        if widget == "addworker":
            return {
                "worker": [dict(EMPTY_ADDWORKER["worker"][0])],
                "instructor_name": "",
            }
        return [dict(EMPTY_LIST_WORKER[0])]
    if widget == "addworker":
        return {
            "worker": [dict(EMPTY_ADDWORKER["worker"][0])],
            "instructor_name": "",
        }
    if field_name == "measureofsafety" or widget == "measureofsafety":
        return dict(EMPTY_MEASURE_OF_SAFETY)
    if field_name == "typeofwork" or widget == "typeofwork":
        return dict(EMPTY_TYPEOFWORK)
    if field_name == "effective_starting" or widget == "daterange_picker":
        return dict(EMPTY_EFFECTIVE_STARTING)
    if field_name in {"firstperiod", "secondperiod"} or widget == "timepicker":
        return dict(EMPTY_PERIOD)
    if field_name == "unit_codes":
        return []
    return ""


def coerce_tenant_field_value(
    field_name: str,
    value: Any,
    *,
    field_type: str | None = None,
) -> Any:
    """Parse JSON-string nested widgets and normalise planneddate dates."""
    import json

    widget = field_type or ""
    parsed = value
    if isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed = value

    if field_name == "typeofwork" or widget == "typeofwork":
        return _coerce_typeofwork_value(parsed)

    if field_name == "planneddate" or widget == "planneddate":
        base = dict(EMPTY_PLANNEDDATE)
        if isinstance(parsed, dict):
            for key in base:
                raw = parsed.get(key)
                if raw is None or raw == "":
                    continue
                if key in {"worktimedate", "completionworkdate"}:
                    base[key] = _to_ddmmyyyy(raw)
                else:
                    base[key] = str(raw)
            # Accept LT aliases the admin view reads.
            for lt_key in (
                "worktimefromLT",
                "worktimetoLT",
                "completionfromLT",
                "completiontoLT",
            ):
                if parsed.get(lt_key) not in (None, ""):
                    base[lt_key] = str(parsed[lt_key])
        return base

    if field_name == "effective_starting" or widget == "daterange_picker":
        if isinstance(parsed, dict):
            return {
                "datefrom": _to_ddmmyyyy(parsed.get("datefrom")),
                "dateto": _to_ddmmyyyy(parsed.get("dateto")),
            }
        return dict(EMPTY_EFFECTIVE_STARTING)

    if field_name in {"firstperiod", "secondperiod"} or widget == "timepicker":
        if isinstance(parsed, dict):
            out = {
                "from": str(parsed.get("from") or ""),
                "to": str(parsed.get("to") or ""),
            }
            for lt_key in ("fromLT", "toLT"):
                if parsed.get(lt_key) not in (None, ""):
                    out[lt_key] = str(parsed[lt_key])
            return out
        return dict(EMPTY_PERIOD)

    if field_name == "workers" or widget in {"listWorker", "addworker"}:
        return parsed if parsed is not None else empty_value_for(field_name, widget)

    if field_name == "measureofsafety" or widget == "measureofsafety":
        if isinstance(parsed, dict):
            base = dict(EMPTY_MEASURE_OF_SAFETY)
            for key, raw in parsed.items():
                base[key] = "" if raw is None else str(raw)
            return base
        return dict(EMPTY_MEASURE_OF_SAFETY)

    return parsed


def is_nested_widget(field_name: str, field_type: str | None = None) -> bool:
    if field_name in {
        "planneddate",
        "workers",
        "measureofsafety",
        "typeofwork",
        "effective_starting",
        "firstperiod",
        "secondperiod",
    }:
        return True
    return (field_type or "") in _NESTED_WIDGETS


# ── Question groups (Handover inspection-group pattern) ───────────────────────

WP_IDENTITY_CONTACT_COLD: Final[tuple[str, ...]] = (
    "department",
    "unit_type",
    "owner",
    "trade_name",
    "responsible_person",
    "phone",
)

WP_WORK_DETAILS_COLD: Final[tuple[str, ...]] = (
    "type_work",
    "list_equipment",
    "work_info",
)

WP_CONTACT_HOT: Final[tuple[str, ...]] = (
    "unit_type",
    "contractor_name",
    "phone",
)

WP_CONTACT_MAINT_HOT: Final[tuple[str, ...]] = (
    "department",
    "unit_type",
    "contractor_name",
    "phone",
)

WP_ROOF_DETAILS: Final[tuple[str, ...]] = (
    "work_type",
    "work_location",
    "demand_appendices",
    "special_conditions",
)

WP_CONTACT_OPS: Final[tuple[str, ...]] = (
    "department",
    "unit_type",
    "phone",
)

WP_SCHEDULE_OPS: Final[tuple[str, ...]] = (
    "effective_starting",
    "firstperiod",
    "secondperiod",
)

# Portal department dropdown (forms.skelton options).
WP_DEPARTMENT_OPTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("Leasing", "leasing"),
    ("Finance", "finance"),
    ("Operations", "operations"),
)

# Ops Type of Work checkboxes (label, portal option.value) — chip order matches form.
WP_TYPEOFWORK_OPTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("Construction", "construction"),
    ("Shop Fitting", "shopfitting"),
    ("Stock Delivery", "stockdelivery"),
    ("Stock Removal", "stockremoval"),
    ("Inventory/Shop Arrangement", "inventory"),
    ("Maintenance inside the shop", "maintenance"),
    ("Others", "others"),
)

# Measure-of-safety checklist keys → chip labels.
WP_MEASURE_OF_SAFETY_LABELS: Final[dict[str, str]] = {
    "fire_extinguisher": "Fire extinguisher",
    "fire_hoses": "Fire hoses",
    "sprinker_on": "Sprinkler on",
    "volatile_remove": "Volatile materials removed",
    "fire_proofcoating": "Fire-proof coating",
    "work_hour_organized": "Work hours organized",
    "equipment_for_carryingout": "Equipment for carrying out",
    "all_Holes_in_floorandWalls": "Holes in floor/walls covered",
}


def field_groups_for_subtype(subtype: str | None) -> tuple[tuple[str, ...], ...]:
    """Ordered multi-field groups for create questions (singletons excluded)."""
    token = str(subtype or "").upper()
    if token in {"CONSTRUCTION_COLD", "MAINTENANCE_COLD"}:
        return (WP_IDENTITY_CONTACT_COLD, WP_WORK_DETAILS_COLD)
    if token == "CONSTRUCTION_HOT":
        return (WP_CONTACT_HOT,)
    if token == "CONSTRUCTION_ROOF_ACCESS":
        return (WP_CONTACT_HOT, WP_ROOF_DETAILS)
    if token in {"MAINTENANCE_HOT", "MAINTENANCE_ROOF_ACCESS"}:
        return (WP_CONTACT_MAINT_HOT,)
    if token == "OPERATIONS":
        return (WP_CONTACT_OPS, WP_SCHEDULE_OPS)
    return ()


def resolve_wp_group(
    subtype: str | None, user_missing: list[str]
) -> tuple[str, ...] | None:
    """Return the open subset of the group that contains ``user_missing[0]``."""
    if not user_missing:
        return None
    first = user_missing[0]
    missing_set = set(user_missing)
    for group in field_groups_for_subtype(subtype):
        if first in group:
            open_fields = tuple(f for f in group if f in missing_set)
            return open_fields if open_fields else None
    return None


# Lease + identity keys the form bridge also needs (beyond FIELD_LABELS).
_WP_DRAFT_EXTRA_KEYS: Final[tuple[str, ...]] = (
    "lease_code",
    "lease_id",
    "property_id",
    "tenant_profile_id",
    "unit_codes",
    "mall",
    "brand",
    "city",
    "company",
    "company_name",
    "lease_brand_mall",
    "contracted_area",
    "sub_category",
    "_wp_type_confirmed",
)


def wp_draft_fields_from_collected(
    collected_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build ``draft_preview.fields`` rows for Work Permit create soft-nav / form sync."""
    collected = collected_data or {}
    keys: list[str] = []
    seen: set[str] = set()
    for key in (*FIELD_LABELS.keys(), *_WP_DRAFT_EXTRA_KEYS):
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)

    rows: list[dict[str, Any]] = []
    for key in keys:
        val = collected.get(key)
        if val is None or val == "" or val == []:
            continue
        # Nested widget dicts (typeofwork, effective_starting, firstperiod, …)
        # must be sent as objects so the frontend form can select/populate the
        # right control. JSON-encoding them to a string produces a raw JSON string
        # that the form cannot interpret (e.g. typeofwork checkboxes stay unchecked).
        # Non-widget lists (unit_codes) are still encoded to strings.
        if isinstance(val, dict):
            display: Any = val
        elif isinstance(val, list):
            display = json.dumps(val)
        else:
            display = val
        rows.append(
            {
                "key": key,
                "label": FIELD_LABELS.get(key, key),
                "value": display,
            }
        )
    return rows
