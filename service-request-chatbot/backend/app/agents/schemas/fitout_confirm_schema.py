"""Fitout Confirmation Service Request — workflow configuration schema.

Single source of truth for:
- Sub-category config map (SUBCAT_CONFIG) — drives all field branching
- Stage definitions (required fields per stage)
- Role assignments
- Allowed extractable fields
- Backend-derived fields
- get_required_fields(), get_missing_fields(), role_can_act_on_stage()

Three sub-categories are supported:
  FIT_OUT_CONFIRM_FOS      — Fitout Start Confirmation (DD_ENGINEER approver)
  FIT_OUT_CONFIRM_END      — Fitout End Confirmation   (DD_ENGINEER approver)
  FIT_OUT_CONFIRM_TRADING  — Trading Confirmation      (MALL_MANAGER / OPERATIONS approver)

All three are single-level workflows: Create path (chatbot → POST) and Approve
path (approver opens IN_PROGRESS SR → PATCH with status: APPROVED).
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Final, Literal

from pydantic import BaseModel, Field as PydanticField, model_validator

# ── Constants ─────────────────────────────────────────────────────────────────

SERVICE_CATEGORY: Final[str] = "FIT_OUT_AND_HANDOVER"
SUB_CATEGORY_FOS: Final[str] = "FIT_OUT_CONFIRM_FOS"
SUB_CATEGORY_END: Final[str] = "FIT_OUT_CONFIRM_END"
SUB_CATEGORY_TRADING: Final[str] = "FIT_OUT_CONFIRM_TRADING"
ASSIGNMENT_TYPE: Final[str] = "workflow"

# ── Literal types ─────────────────────────────────────────────────────────────

WorkflowStage = Literal["CREATE_SR", "APPROVE_SR", "SR_CREATED", "SR_APPROVED", "CANCELLED"]

ConfirmationStatus = Literal["PENDING", "CONFIRMED", "REJECTED", "CANCEL_PENDING"]

# ── Sub-category config map ────────────────────────────────────────────────────
#
# All field-branching logic in validation, payload builder, and missing-field
# nodes is driven by this single config map — no sub_category conditionals
# elsewhere in the codebase.

SUBCAT_CONFIG: dict[str, dict[str, Any]] = {
    "FIT_OUT_CONFIRM_FOS": {
        # Radio field that drives yes/no branch
        "radio_field": "fitout_start_done",
        "radio_label": "Fitout Start Done",
        # YES branch — user supplies the actual date
        "actual_date_field": "actual_fitout_start_date",
        "actual_date_label": "Actual Fitout Start Date",
        # NO branch — user supplies revised planned dates
        "planned_date_fields": [
            "planned_fitout_start_date",
            "planned_fitout_end_date",
            "planned_trading_date",
        ],
        "planned_date_labels": {
            "planned_fitout_start_date": "Planned Fitout Start Date",
            "planned_fitout_end_date": "Planned Fitout End Date",
            "planned_trading_date": "Planned Trading Date",
        },
        # Prior milestone readonly field (populated from GET /contracts)
        "prior_milestone_field": "actual_handover_date_readonly",
        "prior_milestone_label": "Actual Handover Date",
        # Base date fields (readonly, from lease contract)
        "base_date_fields": [
            "base_fitout_start_date",
            "base_fitout_end_date",
            "base_trading_date",
        ],
        # delay_reason is required for FOS when no
        "delay_reason_required": True,
        # FOS and END have a documents section
        "has_documents": True,
        # The chat flow never asks for comments on any fitout confirmation SR
        # (_route_after_validation excludes fitout agents from proactive optional
        # asking), so showing a Comments row on the card is a promise the flow
        # does not keep — it renders permanently blank.
        "has_comments": False,
        # ...but the platform payload still carries the key for FOS/END, so the
        # request shape is unchanged. Kept separate from ``has_comments`` so the
        # display decision cannot silently alter the API contract.
        "payload_has_comments": True,
        "approver_role": "DD_ENGINEER",
        "title_prefix": "Confirm Fitout Start",
    },
    "FIT_OUT_CONFIRM_END": {
        "radio_field": "fitout_complete_done",
        "radio_label": "Fitout Complete Done",
        "actual_date_field": "actual_fitout_complete_date",
        "actual_date_label": "Actual Fitout Complete Date",
        "planned_date_fields": [
            "planned_fitout_complete_date",
            "planned_trading_date",
        ],
        "planned_date_labels": {
            "planned_fitout_complete_date": "Planned Fitout Complete Date",
            "planned_trading_date": "Planned Trading Date",
        },
        "prior_milestone_field": "actual_fitout_start_date_readonly",
        "prior_milestone_label": "Actual Fitout Start Date",
        "base_date_fields": [
            "base_fitout_end_date",
            "base_trading_date",
        ],
        "delay_reason_required": True,
        "has_documents": True,
        # See FIT_OUT_CONFIRM_FOS above — never asked, so never displayed, but
        # still sent in the payload.
        "has_comments": False,
        "payload_has_comments": True,
        "approver_role": "DD_ENGINEER",
        "title_prefix": "Confirm Fitout End",
    },
    "FIT_OUT_CONFIRM_TRADING": {
        "radio_field": "trading_done",
        "radio_label": "Trading Done",
        "actual_date_field": "actual_trading_date",
        "actual_date_label": "Actual Trading Date",
        "planned_date_fields": [
            "planned_trading_date",
        ],
        "planned_date_labels": {
            "planned_trading_date": "Planned Trading Date",
        },
        "prior_milestone_field": "actual_fitout_end_date_readonly",
        "prior_milestone_label": "Actual Fitout End Date",
        "base_date_fields": [
            "base_trading_date",
        ],
        # delay_reason is mandatory on the NO branch (same as FOS/END)
        "delay_reason_required": True,
        # TRADING does NOT have documents or comments sections
        "has_documents": False,
        "has_comments": False,
        "payload_has_comments": False,
        "approver_role": "MALL_MANAGER",  # also OPERATIONS — see PERMISSION_MAP below
        "title_prefix": "Confirm Trading",
    },
}

# ── Stage definition dataclass ────────────────────────────────────────────────


@dataclass(frozen=True)
class StageDefinition:
    """Immutable description of a single workflow stage."""

    stage: str
    required_fields: tuple[str, ...]
    required_documents: tuple[str, ...] = dc_field(default_factory=tuple)


# ── Base required fields (lease + radio) shared by all sub-categories ─────────

_LEASE_FIELDS: tuple[str, ...] = (
    "lease_brand_mall",
    "lease_code",
    "lease_id",
)

# ── Stage instances ───────────────────────────────────────────────────────────
# The required_fields for CREATE_SR are the minimum common fields; sub-category
# branching (actual date vs planned dates + delay_reason) is resolved at
# runtime by get_required_fields() using the sub_category from state.

CREATE_SR_STAGE = StageDefinition(
    stage="CREATE_SR",
    required_fields=_LEASE_FIELDS + ("radio_field",),  # placeholder — see get_required_fields()
)

APPROVE_SR_STAGE = StageDefinition(
    stage="APPROVE_SR",
    required_fields=_LEASE_FIELDS + ("radio_field",),  # same dynamic resolution
)

# ── Registry / ordered stage list ────────────────────────────────────────────

STAGE_REGISTRY: dict[str, StageDefinition] = {
    "CREATE_SR": CREATE_SR_STAGE,
    "APPROVE_SR": APPROVE_SR_STAGE,
}

WORKFLOW_STAGES: tuple[str, ...] = ("CREATE_SR", "APPROVE_SR")

TERMINAL_STAGES: frozenset[str] = frozenset({"SR_CREATED", "SR_APPROVED", "CANCELLED"})

# ── Derived field sets ────────────────────────────────────────────────────────

# All fields that the LLM / user may supply across every sub-category.
ALLOWED_EXTRACTED_FIELDS: frozenset[str] = frozenset(
    {
        # Lease identification
        "lease_code",
        "lease_brand_mall",
        "mall",
        "brand",
        "company",
        # FOS radio + dates
        "fitout_start_done",
        "actual_fitout_start_date",
        "planned_fitout_start_date",
        "planned_fitout_end_date",
        "planned_trading_date",
        # END radio + dates
        "fitout_complete_done",
        "actual_fitout_complete_date",
        "planned_fitout_complete_date",
        # TRADING radio + dates
        "trading_done",
        "actual_trading_date",
        # Shared optional
        "delay_reason",
        "comments",
    }
)

# Fields populated from backend APIs — never asked of the user.
BACKEND_DERIVED_FIELDS: frozenset[str] = frozenset(
    {
        "lease_id",
        "lease_code",
        "mall",
        "brand",
        "company_name",
        "brand_name",
        "mall_name",
        # Readonly reference dates from GET /contracts
        "actual_handover_date_readonly",
        "actual_fitout_start_date_readonly",
        "actual_fitout_end_date_readonly",
        # Base dates from lease contract
        "base_fitout_start_date",
        "base_fitout_end_date",
        "base_trading_date",
        # Lease display label
        "lease",
        "pms_tenant_code",
    }
)

# Fields the user explicitly supplies.
USER_SUPPLIED_FIELDS: frozenset[str] = ALLOWED_EXTRACTED_FIELDS - BACKEND_DERIVED_FIELDS

# Optional fields — an empty-string value is acceptable (user may skip).
OPTIONAL_FIELDS: frozenset[str] = frozenset({"comments"})

# Fields that must only ever come from backend lookups.
BACKEND_ONLY_FIELDS: frozenset[str] = frozenset(
    {
        "lease_id",
        "company_name",
        "brand_name",
        "mall_name",
        "pms_tenant_code",
        "actual_handover_date_readonly",
        "actual_fitout_start_date_readonly",
        "actual_fitout_end_date_readonly",
        "base_fitout_start_date",
        "base_fitout_end_date",
        "base_trading_date",
    }
)

# ── Permission map ────────────────────────────────────────────────────────────

PERMISSION_MAP: dict[str, tuple[str, ...]] = {
    "DD_ENGINEER": ("CREATE_SR", "APPROVE_SR"),       # FOS and END
    "MALL_MANAGER": ("CREATE_SR", "APPROVE_SR"),      # TRADING
    "OPERATIONS": ("CREATE_SR", "APPROVE_SR"),        # TRADING (shared with MALL_MANAGER)
}

# Sub-category → roles that may approve
APPROVE_ROLE_MAP: dict[str, frozenset[str]] = {
    "FIT_OUT_CONFIRM_FOS": frozenset({"DD_ENGINEER"}),
    "FIT_OUT_CONFIRM_END": frozenset({"DD_ENGINEER"}),
    "FIT_OUT_CONFIRM_TRADING": frozenset({"MALL_MANAGER", "OPERATIONS"}),
}

# ── Helper utilities ──────────────────────────────────────────────────────────


def get_required_fields(stage_name: str, sub_category: str) -> tuple[str, ...]:
    """Return required fields for *stage_name* and *sub_category*.

    The field set is dynamic because:
    - YES branch requires the actual date field.
    - NO branch requires planned date fields + conditionally delay_reason.
    - Prior collected data is not available here — validation handles the
      conditional branch via get_missing_fields().

    This function returns all fields that COULD be required (lease + radio +
    both branches) so that validation can decide which subset applies.
    """
    cfg = SUBCAT_CONFIG.get(sub_category, {})
    radio = cfg.get("radio_field", "")
    actual = cfg.get("actual_date_field", "")
    planned = tuple(cfg.get("planned_date_fields", []))

    base = _LEASE_FIELDS + (radio,)
    if actual:
        base = base + (actual,)
    base = base + planned
    if cfg.get("delay_reason_required", False):
        base = base + ("delay_reason",)
    if cfg.get("has_comments", False):
        base = base + ("comments",)
    return base


def get_missing_fields(stage_name: str, sub_category: str, collected: dict) -> list[str]:
    """Return required fields absent from *collected* for the given stage + sub_category.

    Branching logic:
    - radio_field is always required.
    - When radio_field == "yes": actual_date_field is required;
      planned_date_fields and delay_reason are NOT required.
    - When radio_field == "no": planned_date_fields are required;
      actual_date_field is NOT required;
      delay_reason is required only if SUBCAT_CONFIG says so.
    - When radio_field is not yet answered: ask for it (all branch fields deferred).
    """

    def _is_missing(value: object) -> bool:
        return value is None or value == "" or value == []

    def _is_missing_for_field(fname: str, value: object) -> bool:
        if fname in OPTIONAL_FIELDS:
            return value is None
        return _is_missing(value)

    cfg = SUBCAT_CONFIG.get(sub_category, {})
    if not cfg:
        return []

    radio_field = cfg.get("radio_field", "")
    radio_value = collected.get(radio_field, "")
    missing: list[str] = []

    # CREATE: lease_brand_mall triggers lease lookup.
    # APPROVE: open-SR hydration may only have lease_code / lease_id — treat
    # either as sufficient so we do not re-ask for the lease on approve.
    if stage_name == "APPROVE_SR":
        has_lease = (
            not _is_missing(collected.get("lease_brand_mall"))
            or not _is_missing(collected.get("lease_code"))
            or not _is_missing(collected.get("lease_id"))
        )
        if not has_lease:
            missing.append("lease_brand_mall")
    else:
        if _is_missing(collected.get("lease_brand_mall")):
            missing.append("lease_brand_mall")

    # Radio field itself
    if _is_missing(radio_value):
        missing.append(radio_field)
        return missing  # cannot determine branch until radio is answered

    # CREATE + APPROVE: form/bridge may pre-fill yes/no (and planned dates), but
    # the user must still explicitly answer the radio in this chat session.
    # ``_approve_radio_confirmed`` is set by merge_state when they answer.
    if (
        stage_name in ("CREATE_SR", "APPROVE_SR")
        and not collected.get("_approve_radio_confirmed")
    ):
        missing.append(radio_field)
        return missing

    # YES branch
    if radio_value == "yes":
        actual = cfg.get("actual_date_field", "")
        if actual and _is_missing(collected.get(actual)):
            missing.append(actual)
        # During APPROVE_SR the approver is reviewing, not filling in creation-phase
        # optional fields.  Only ask for comments at CREATE_SR stage.
        if stage_name == "CREATE_SR" and cfg.get("has_comments") and _is_missing_for_field("comments", collected.get("comments")):
            missing.append("comments")
        return missing

    # NO branch
    if radio_value == "no":
        for pf in cfg.get("planned_date_fields", []):
            if _is_missing(collected.get(pf)):
                missing.append(pf)
        # delay_reason: required on NO when SUBCAT_CONFIG.delay_reason_required
        if cfg.get("delay_reason_required"):
            if _is_missing(collected.get("delay_reason")):
                missing.append("delay_reason")
        else:
            # optional — ask if never answered (None), not if empty-string
            if _is_missing_for_field("delay_reason", collected.get("delay_reason")):
                missing.append("delay_reason")
        if stage_name == "CREATE_SR" and cfg.get("has_comments") and _is_missing_for_field("comments", collected.get("comments")):
            missing.append("comments")
        return missing

    # Unrecognised radio value — treat as missing
    missing.append(radio_field)
    return missing


def role_can_act_on_stage(role: str, stage_name: str) -> bool:
    """True if *role* is permitted to act on *stage_name*."""
    return stage_name in PERMISSION_MAP.get(role, ())


def role_can_approve(role: str, sub_category: str) -> bool:
    """True if *role* may approve the given sub_category SR."""
    return role in APPROVE_ROLE_MAP.get(sub_category, frozenset())


def build_title(sub_category: str, pms_tenant_code: str) -> str:
    """Return the auto-generated SR title per the platform convention."""
    cfg = SUBCAT_CONFIG.get(sub_category, {})
    prefix = cfg.get("title_prefix", "Confirm Fitout")
    return f"{prefix} - {pms_tenant_code}" if pms_tenant_code else prefix


# ── LLM extraction output shape (Pydantic) ────────────────────────────────────


class ExtractedFieldValue(BaseModel):
    value: str = PydanticField(description="Proposed string value for this field.")
    confidence: float = PydanticField(default=1.0, ge=0.0, le=1.0)


class FitoutConfirmExtractedFields(BaseModel):
    """Rich LLM extraction output for fitout confirm workflows.

    The model_validator silently removes any field that is not in
    ALLOWED_EXTRACTED_FIELDS or is present in BACKEND_ONLY_FIELDS.
    """

    summary: str | None = PydanticField(
        default=None,
        description="One-sentence summary of user intent.",
    )
    fields: dict[str, ExtractedFieldValue] = PydanticField(
        default_factory=dict,
        description="Mapping of field_name → {value, confidence}.",
    )

    @model_validator(mode="after")
    def _strip_forbidden_and_unknown_fields(self) -> "FitoutConfirmExtractedFields":
        self.fields = {
            k: v
            for k, v in self.fields.items()
            if k in ALLOWED_EXTRACTED_FIELDS and k not in BACKEND_ONLY_FIELDS
        }
        return self

    def to_flat_dict(self) -> dict[str, str]:
        return {k: v.value for k, v in self.fields.items()}

    def to_confidence_dict(self) -> dict[str, float]:
        return {k: v.confidence for k, v in self.fields.items()}
