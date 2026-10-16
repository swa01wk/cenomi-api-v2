"""Ceiling Inspection Service Request — workflow configuration schema.

Single source of truth for the ``rdd_ceiling_inspection_agent``:
- Sub-category map (CEILING_SUBCAT_CONFIG) — the only difference between
  Open Ceiling and Close Ceiling. Every node reads this map; none branches on
  the sub-category itself.
- Stage names, terminal stages, required create fields.
- Who may create / review / acknowledge (create: Mall Manager, FM Manager,
  RDD PM; acknowledge: tenants only).
- get_missing_fields(), detect_sub_category(), review helpers.

Platform facts (cenomi-api, migration 1733723567 + service_request_usecase.go)
------------------------------------------------------------------------------
- Create posts ``status = IN_PROCESS``; ``inspection_done_by`` is a read-only
  role id on the form (its skeleton ``value``) and never asked. The assistant
  reads it from the form (services/ceiling_inspection_done_by.py).
- Level 1 is DD_ENGINEER (role 12), level 2 is FM_MANAGER (role 20).
- Level 2 APPROVED sets the SR to ``REPORT_SUBMITTED`` for both sub-categories.
- A tenant PATCH with ``status = APPROVED`` on a ``REPORT_SUBMITTED`` SR is the
  acknowledgement that completes it.
- The draft report is uploaded by DD_ENGINEER with status APPROVED or
  APPROVED_WITH_CHANGES. The rejected report carries DISAPPROVED.
"""

from __future__ import annotations

import re
from typing import Any, Final

# ── Identity ──────────────────────────────────────────────────────────────────

AGENT_NAME: Final[str] = "rdd_ceiling_inspection_agent"
SERVICE_CATEGORY: Final[str] = "FIT_OUT_AND_HANDOVER"
SUB_CATEGORY_OCI: Final[str] = "OPEN_CEILING_INSPECTION"
SUB_CATEGORY_CCI: Final[str] = "CLOSE_CEILING_INSPECTION"
# Routing placeholder for "raise a ceiling inspection" with neither name. The
# entry node never lets it reach a payload — it asks which inspection first.
SUB_CATEGORY_UNRESOLVED: Final[str] = "CEILING_INSPECTION"

# Form field ``inspection_done_by`` is a ReadOnlyRoleField holding a role id.
# Used only when the form's own value can't be read.
INSPECTION_DONE_BY_ROLE_ID: Final[int] = 12


def inspection_done_by_role_id(collected: dict[str, Any]) -> int:
    """Role id seeded into ``collected["inspection_done_by"]`` from the form.

    A missing or non-numeric value falls back to ``INSPECTION_DONE_BY_ROLE_ID``.
    """
    raw = collected.get("inspection_done_by")
    if raw in (None, ""):
        return INSPECTION_DONE_BY_ROLE_ID
    try:
        return int(raw)
    except (TypeError, ValueError):
        return INSPECTION_DONE_BY_ROLE_ID
OTHER_DOC_TYPE: Final[str] = "FITOUT_INSPECTION_OTHER"

# ── Sub-category map (PRD §2.1) ──────────────────────────────────────────────

CEILING_SUBCAT_CONFIG: dict[str, dict[str, Any]] = {
    SUB_CATEGORY_OCI: {
        "label": "Open Ceiling Inspection",
        "short_label": "OCI",
        "draft_report_doc": "FITOUT_OCI_REPORT_DRAFT",
        "rejected_report_doc": "SR_REJECTED_OCI_REPORT",
        "sharepoint_folder": "OCI",
    },
    SUB_CATEGORY_CCI: {
        "label": "Close Ceiling Inspection",
        "short_label": "CCI",
        "draft_report_doc": "FITOUT_CCI_REPORT_DRAFT",
        "rejected_report_doc": "SR_REJECTED_CCI_REPORT",
        "sharepoint_folder": "CCI",
    },
}

CEILING_SUB_CATEGORIES: frozenset[str] = frozenset(CEILING_SUBCAT_CONFIG)

# Every document type this agent may attach, across both sub-categories.
DRAFT_REPORT_DOC_TYPES: frozenset[str] = frozenset(
    cfg["draft_report_doc"] for cfg in CEILING_SUBCAT_CONFIG.values()
)
REJECTED_REPORT_DOC_TYPES: frozenset[str] = frozenset(
    cfg["rejected_report_doc"] for cfg in CEILING_SUBCAT_CONFIG.values()
)
ALL_CEILING_DOC_TYPES: frozenset[str] = (
    DRAFT_REPORT_DOC_TYPES | REJECTED_REPORT_DOC_TYPES | {OTHER_DOC_TYPE}
)

# ── Stages ────────────────────────────────────────────────────────────────────

STAGE_CREATE: Final[str] = "CREATE_CEILING_INSPECTION_SR"
STAGE_L1_REVIEW: Final[str] = "CEILING_L1_REVIEW"
STAGE_L2_REVIEW: Final[str] = "CEILING_L2_REVIEW"
STAGE_REPORT_SUBMITTED: Final[str] = "CEILING_REPORT_SUBMITTED"
STAGE_COMPLETED: Final[str] = "CEILING_COMPLETED"
# The creator's (or reviewer's) own turn is over once the platform accepted it.
# The SR itself carries on; whoever opens it next is synced to the live stage.
STAGE_SR_CREATED: Final[str] = "CEILING_SR_CREATED"
STAGE_REVIEW_SUBMITTED: Final[str] = "CEILING_REVIEW_SUBMITTED"
STAGE_CANCELLED: Final[str] = "CANCELLED"

REVIEW_STAGES: frozenset[str] = frozenset({STAGE_L1_REVIEW, STAGE_L2_REVIEW})
TERMINAL_STAGES: frozenset[str] = frozenset(
    {STAGE_SR_CREATED, STAGE_REVIEW_SUBMITTED, STAGE_COMPLETED, STAGE_CANCELLED}
)

# Which role acts at each review level (PRD §2 table).
LEVEL_ROLE: dict[str, str] = {
    STAGE_L1_REVIEW: "DD_ENGINEER",
    STAGE_L2_REVIEW: "FM_MANAGER",
}
LEVEL_ROLE_LABEL: dict[str, str] = {
    STAGE_L1_REVIEW: "RDD Project Manager",
    STAGE_L2_REVIEW: "FM Manager",
}

# ── Intents ───────────────────────────────────────────────────────────────────

INTENT_CREATE: Final[str] = "CREATE_CEILING_INSPECTION_SR"
INTENT_REVIEW: Final[str] = "REVIEW_CEILING_INSPECTION_SR"
INTENT_APPROVE: Final[str] = "APPROVE_CEILING_INSPECTION_SR"
INTENT_ACKNOWLEDGE: Final[str] = "ACKNOWLEDGE_CEILING_INSPECTION_SR"

ACTION_INTENTS: frozenset[str] = frozenset(
    {INTENT_CREATE, INTENT_REVIEW, INTENT_APPROVE, INTENT_ACKNOWLEDGE}
)

# ── RBAC (PRD §3.2) ───────────────────────────────────────────────────────────

# Only the tenant acknowledges the submitted report. These are every role the
# roles fixture marks ``user_type: TENANT``, plus SHOP_MANAGER, the role name
# live AI-session JWTs stamp for the "Shop Manager / Company Admin" tenant user.
TENANT_ROLES: frozenset[str] = frozenset(
    {
        "PRIMARY_TENANT_CONTACT",
        "AUTHORIZED_SIGNATORY",
        "BILLING_CONTACT",
        "FINANCIAL_CONTROLLER",
        "COMPANY_ADMIN_CENTRAL",
        "COMPANY_ADMIN",
        "LEASING_MANAGER",
        "SERVICE_PROVIDER",
        "MARKETING_CONTACT",
        "SHOP_MANAGER",
    }
)
# Mall Manager, FM Manager and the RDD Project Manager (DD_ENGINEER) raise a
# ceiling inspection; tenants do not. The creator's own approval level is then
# auto-approved by the platform (creator-role rule in cenomi-api) — for RDD PM
# only when the draft report comes with the create, which is why RDD PM is asked
# for it (creator_uploads_report). The permission string CAN_RAISE_HANDOVER_SR
# is not the gate: RDD_CONTRACTOR holds it too.
CREATE_ROLES: frozenset[str] = frozenset({"MALL_MANAGER", "FM_MANAGER", "DD_ENGINEER"})
ACK_ROLES: frozenset[str] = TENANT_ROLES

# ── Create form fields (form skeleton, both sub-categories) ──────────────────

LEASE_TRIGGER_FIELD: Final[str] = "lease_brand_mall"

# Asked in this order. Start and end are separate questions (PRD §3.5.3).
CREATE_REQUIRED_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "startDate",
    "endDate",
    "comments",
)
CREATE_OPTIONAL_FIELDS: tuple[str, ...] = ("notes",)

# ``notes`` is optional on the form. An empty string is an answer.
OPTIONAL_FIELDS: frozenset[str] = frozenset(CREATE_OPTIONAL_FIELDS)

# Resolved by the lease lookup — never asked.
BACKEND_DERIVED_FIELDS: frozenset[str] = frozenset(
    {
        "lease_id",
        "lease",
        "mall",
        "brand",
        "brand_id",
        "tenant_profile_id",
        "property_id",
        "unit_codes",
        "city",
        "contracted_area",
        "company_name",
        "brand_name",
        "mall_name",
        "pms_tenant_code",
        "lease_brand_mall",
    }
)

# ── Review / acknowledgement vocabulary ──────────────────────────────────────

DOC_STATUS_APPROVED: Final[str] = "APPROVED"
DOC_STATUS_APPROVED_WITH_CHANGES: Final[str] = "APPROVED_WITH_CHANGES"
DOC_STATUS_DISAPPROVED: Final[str] = "DISAPPROVED"
DRAFT_REPORT_STATUSES: tuple[str, ...] = (
    DOC_STATUS_APPROVED,
    DOC_STATUS_APPROVED_WITH_CHANGES,
)

# RDD PM create: the draft report's status, held in collected_data until the
# create payload sends it in document_status_map.
DRAFT_REPORT_STATUS_KEY: Final[str] = "draft_report_status"

# Review actions. Reject is not one of them (PRD §2.5).
REVIEW_ACTION_APPROVE: Final[str] = "approve"
REVIEW_ACTION_MORE_INFO: Final[str] = "more_info"

# Platform statuses the review PATCH sends for each action.
REVIEW_ACTION_PLATFORM_STATUS: dict[str, str] = {
    REVIEW_ACTION_APPROVE: "APPROVED",
    REVIEW_ACTION_MORE_INFO: "ACTION_REQUIRED",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

_OCI_RE = re.compile(r"\b(open[\s-]*ceiling|oci)\b", re.IGNORECASE)
_CCI_RE = re.compile(r"\b(close[ds]?[\s-]*ceiling|cci)\b", re.IGNORECASE)


def detect_sub_category(text: str | None) -> str | None:
    """Return the ceiling sub-category named in *text*, or ``None``.

    "open ceiling" / "OCI" → Open; "close(d) ceiling" / "CCI" → Close. Both
    named, or neither, returns ``None`` so the caller asks — it never guesses.
    """
    if not text:
        return None
    is_open = bool(_OCI_RE.search(text))
    is_close = bool(_CCI_RE.search(text))
    if is_open == is_close:
        return None
    return SUB_CATEGORY_OCI if is_open else SUB_CATEGORY_CCI


_CEILING_MENTION_RE = re.compile(r"\b(ceiling|oci|cci)\b", re.IGNORECASE)


def mentions_ceiling_inspection(text: str | None) -> bool:
    """True when *text* is about a ceiling inspection, Open or Close or unnamed."""
    return bool(text and _CEILING_MENTION_RE.search(text))


# Fixed reply when the LLM provider refuses a ceiling inspection turn for lack
# of credits (HTTP 429 insufficient_quota). Sent verbatim — no model is needed.
OUT_OF_CREDITS_MESSAGE: Final[str] = (
    "You are out of credits currently. The AI service could not process your "
    "ceiling inspection request — please try again once credits have been added."
)
_OUT_OF_CREDITS_MARKERS: tuple[str, ...] = ("insufficient_quota", "credit_balance_exhausted")


def is_out_of_credits_error(error: str | None) -> bool:
    return bool(error) and any(marker in error for marker in _OUT_OF_CREDITS_MARKERS)


def is_resolved_sub_category(sub_category: str | None) -> bool:
    return (sub_category or "") in CEILING_SUBCAT_CONFIG


def sub_category_label(sub_category: str | None) -> str:
    cfg = CEILING_SUBCAT_CONFIG.get(sub_category or "")
    return cfg["label"] if cfg else "Ceiling Inspection"


def draft_report_doc_type(sub_category: str | None) -> str | None:
    cfg = CEILING_SUBCAT_CONFIG.get(sub_category or "")
    return cfg["draft_report_doc"] if cfg else None


def rejected_report_doc_type(sub_category: str | None) -> str | None:
    cfg = CEILING_SUBCAT_CONFIG.get(sub_category or "")
    return cfg["rejected_report_doc"] if cfg else None


def _is_missing(value: object) -> bool:
    return value is None or value == "" or value == []


def get_missing_fields(
    stage_name: str,
    sub_category: str | None,
    collected: dict[str, Any],
) -> list[str]:
    """Return required create fields absent from *collected*.

    Only the create stage collects form fields through the shared pipeline.
    Review and acknowledgement are driven by the review entry node, so this
    returns an empty list for them.
    """
    if stage_name != STAGE_CREATE:
        return []
    missing: list[str] = []
    if _is_missing(collected.get(LEASE_TRIGGER_FIELD)) and _is_missing(
        collected.get("lease_id")
    ):
        missing.append(LEASE_TRIGGER_FIELD)
    for fname in CREATE_REQUIRED_FIELDS:
        if _is_missing(collected.get(fname)):
            missing.append(fname)
    # ``notes`` is optional and not asked (PRD §5.1 goes from comments straight
    # to the card). The user can add it in chat or on the confirmation card.
    return missing


def _date_part(value: Any) -> str:
    """``YYYY-MM-DD`` from an ISO date or datetime string, else ``""``."""
    text = str(value or "").strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else ""


def validate_date_order(collected: dict[str, Any]) -> list[dict[str, Any]]:
    """End date must be on or after the start date (PRD §4)."""
    start = _date_part(collected.get("startDate"))
    end = _date_part(collected.get("endDate"))
    if not start or not end or end >= start:
        return []
    return [
        {
            "field": "endDate",
            "validation_type": "ceiling_date_order",
            "status": "FAILED",
            "message": (
                f"The end date ({_display_date(end)}) is before the start date "
                f"({_display_date(start)}). Please give an end date on or after "
                f"{_display_date(start)}, in DD/MM/YYYY format."
            ),
            "blocking": True,
        }
    ]


def _display_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def user_roles(auth_context: Any) -> set[str]:
    return {str(r).upper() for r in (getattr(auth_context, "roles", None) or ())}


def can_create(auth_context: Any) -> bool:
    """True when the caller may raise either ceiling inspection (CREATE_ROLES).

    An unresolved role stays permissive, matching every other entry guard.
    """
    roles = user_roles(auth_context)
    if not roles:
        return True
    return bool(roles & CREATE_ROLES)


def creator_uploads_report(auth_context: Any) -> bool:
    """True when the creator is the RDD Project Manager (level 1).

    RDD PM's level is auto-approved at create only when the draft report and its
    status come with the request, so the create flow asks RDD PM for both.
    """
    return "DD_ENGINEER" in user_roles(auth_context)


def is_tenant(auth_context: Any) -> bool:
    return bool(user_roles(auth_context) & ACK_ROLES)


def uploaded_doc_types(documents: list[dict[str, Any]] | None) -> set[str]:
    return {
        str(d.get("document_type_id") or d.get("document_type") or "")
        for d in (documents or [])
        if isinstance(d, dict) and (d.get("document_id") or d.get("id"))
    }


def find_document(
    documents: list[dict[str, Any]] | None, doc_type: str | None
) -> dict[str, Any] | None:
    if not doc_type:
        return None
    for doc in documents or []:
        if not isinstance(doc, dict):
            continue
        if (doc.get("document_type_id") or doc.get("document_type")) == doc_type and (
            doc.get("document_id") or doc.get("id")
        ):
            return doc
    return None


def has_draft_report(documents: list[dict[str, Any]] | None, sub_category: str | None) -> bool:
    return find_document(documents, draft_report_doc_type(sub_category)) is not None


# ── Platform status → chat stage (used by sr_status_sync) ────────────────────

_ROLE_TO_STAGE: dict[str, str] = {
    "DD_ENGINEER": STAGE_L1_REVIEW,
    "12": STAGE_L1_REVIEW,
    "FM_MANAGER": STAGE_L2_REVIEW,
    "20": STAGE_L2_REVIEW,
}
_LEVEL_TO_STAGE: dict[int, str] = {1: STAGE_L1_REVIEW, 2: STAGE_L2_REVIEW}
_ACTIVE_OPERATION_STATUSES: frozenset[str] = frozenset({"IN_PROGRESS", "PENDING"})
_COMPLETED_STATUSES: frozenset[str] = frozenset({"CLOSED", "COMPLETED"})


def map_operations_to_stage(
    operations: list[dict[str, Any]],
    platform_status: str | None,
    current_stage: str | None,
) -> str | None:
    """Derive the ceiling chat stage from the platform SR.

    The assigned role decides the level (DD_ENGINEER → level 1, FM_MANAGER →
    level 2), not the workflow_level number: the shared workflow row has been
    written in reverse order in some environments (PRD §2), and the review
    rules belong to the role. The level number is only a fallback for an
    operation whose role cannot be read.
    """
    status = (platform_status or "").upper()
    if status in _COMPLETED_STATUSES:
        return STAGE_COMPLETED
    if status == "CANCELLED":
        return STAGE_CANCELLED
    if status == "REPORT_SUBMITTED":
        return STAGE_REPORT_SUBMITTED

    active = [
        op
        for op in operations
        if str(op.get("status") or "").upper() in _ACTIVE_OPERATION_STATUSES
    ]
    active.sort(key=lambda op: int(op.get("workflow_level") or 0))
    for op in active:
        role = str(op.get("assigned_role") or op.get("role") or "").strip().upper()
        if role in _ROLE_TO_STAGE:
            return _ROLE_TO_STAGE[role]
    for op in active:
        stage = _LEVEL_TO_STAGE.get(int(op.get("workflow_level") or 0))
        if stage:
            return stage
    return current_stage
