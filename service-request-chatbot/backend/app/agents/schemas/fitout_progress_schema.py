"""Fitout Progress Service Request — workflow configuration schema.

Single source of truth for:
- Stage definitions (required fields per stage)
- Role assignments (create and approve are both DD_ENGINEER only)
- Allowed extractable fields / backend-derived fields
- get_required_fields(), get_missing_fields(), role_can_act_on_stage(),
  role_can_approve(), build_title()

One sub-category, one human field::

    FITOUT_PROGRESS — "how far along is this lease's fit-out, as a percentage"

Everything else on the platform form is read-only lease reference data that
``lease_lookup_node`` already fills, so the flow asks exactly one question.
See ``docs/specs/fitout-progress-agent-prd.md``.

Two stages, both single-level:

``CREATE_SR``
    The RDD PM records the percentage → POST. Because the creator holds the
    approver role, the platform auto-approves inside the create transaction and
    writes ``leases.fitout_progress`` — a create here is a *recording*, not a
    request (``AutoApproveFitoutProgressForApproverCreator``).
``APPROVE_SR``
    An SR raised by the weekly scheduler or by a non-DD admin is waiting at
    level 1 → PATCH with ``APPROVED`` (optionally with a corrected percentage,
    which is what the web form's *Update and Approve* button does) or
    ``REJECTED``.

Deliberately absent, because the platform workflow has no such action and any
attempt is a 400: cancel, reassign, action-required, re-upload, and a
payload-only save (``IN_PROCESS``). There are no attachments.

The percentage's own parsing / suggestion arithmetic lives in
``app/agents/services/fitout_progress_suggestions_service.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Final, Literal

from pydantic import BaseModel, Field as PydanticField, model_validator

# ── Constants ─────────────────────────────────────────────────────────────────

SERVICE_CATEGORY: Final[str] = "FIT_OUT_AND_HANDOVER"
SUB_CATEGORY: Final[str] = "FITOUT_PROGRESS"
ASSIGNMENT_TYPE: Final[str] = "workflow"

# The one field a human supplies. Platform sends/expects it as a *string*
# ("82"), and stores it in NUMERIC(5,2) columns on ``leases`` and
# ``lease_planned_dates_tracking``.
PROGRESS_FIELD: Final[str] = "fitout_progress_percentage"

# Note the singular: the FITOUT_PROGRESS form skeleton uses ``comment``, where
# the fitout *confirmation* sub-categories use ``comments``. Never asked for —
# only captured when the user volunteers it in the same sentence.
COMMENT_FIELD: Final[str] = "comment"

# Title convention from the platform scheduler: "Fitout Progress - <lease_code>".
TITLE_PREFIX: Final[str] = "Fitout Progress"

# ── Literal types ─────────────────────────────────────────────────────────────

WorkflowStage = Literal[
    "CREATE_SR",
    "APPROVE_SR",
    "SR_CREATED",
    "SR_APPROVED",
    "SR_REJECTED",
]

ConfirmationStatus = Literal["PENDING", "CONFIRMED", "REJECTED"]

# ── Stage definitions ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StageDefinition:
    """Immutable description of a single workflow stage."""

    stage: str
    required_fields: tuple[str, ...]
    required_documents: tuple[str, ...] = dc_field(default_factory=tuple)


_LEASE_FIELDS: Final[tuple[str, ...]] = (
    "lease_brand_mall",
    "lease_code",
    "lease_id",
)

CREATE_SR_STAGE = StageDefinition(
    stage="CREATE_SR",
    required_fields=_LEASE_FIELDS + (PROGRESS_FIELD,),
)

# The approver's SR already carries the lease; only the percentage is required,
# and it must be answered in this session even though the stored payload
# pre-fills it — see ``PROGRESS_CONFIRMED_KEY``.
APPROVE_SR_STAGE = StageDefinition(
    stage="APPROVE_SR",
    required_fields=(PROGRESS_FIELD,),
)

STAGE_REGISTRY: dict[str, StageDefinition] = {
    "CREATE_SR": CREATE_SR_STAGE,
    "APPROVE_SR": APPROVE_SR_STAGE,
}

WORKFLOW_STAGES: Final[tuple[str, ...]] = ("CREATE_SR", "APPROVE_SR")

TERMINAL_STAGES: frozenset[str] = frozenset(
    {"SR_CREATED", "SR_APPROVED", "SR_REJECTED"}
)

# Stages where a pre-filled percentage is not an answer — which is both of
# them.
#
# On APPROVE_SR the number arrives in the stored payload, and approving it
# without asking would approve a figure nobody in the conversation ever saw.
# On CREATE_SR it arrives from the *form*: the web form computes the same
# elapsed-time default this agent suggests and writes it into the field the
# moment a lease is picked (``computeFitoutProgressDefault`` in
# ``ServiceReqForms.tsx``), and the form→chat bridge then syncs it back. Left
# counting as an answer, that reached the review card without the user ever
# being asked — reported live on DUMMY-CUST-001, which went straight from the
# question to "Recording fit-out progress at 100%".
#
# ``merge_state`` sets the flag only for a turn the user actually took.
PROGRESS_CONFIRMED_KEY: Final[str] = "_progress_confirmed"
STAGES_REQUIRING_EXPLICIT_PROGRESS: frozenset[str] = frozenset(
    {"CREATE_SR", "APPROVE_SR"}
)

# ── Derived field sets ────────────────────────────────────────────────────────

# Everything the LLM / user may supply.
ALLOWED_EXTRACTED_FIELDS: frozenset[str] = frozenset(
    {
        # Lease identification
        "lease_code",
        "lease_brand_mall",
        "mall",
        "brand",
        "company",
        # The one answer
        PROGRESS_FIELD,
        # Volunteered only
        COMMENT_FIELD,
    }
)

# Fields populated from backend lookups — never asked of the user.
BACKEND_DERIVED_FIELDS: frozenset[str] = frozenset(
    {
        "lease_id",
        "lease_code",
        "contract_id",
        "tenant_profile_id",
        "property_id",
        "mall",
        "brand",
        "company",
        "company_name",
        "brand_name",
        "mall_name",
        "pms_tenant_code",
        "lease",
        # Resolved by the lease lookup, but still a trigger the user may type —
        # ``lease_trigger_fields`` is what makes missing_field_node ask for it.
        "lease_brand_mall",
        # Session-only flag — never ask the user for this.
        PROGRESS_CONFIRMED_KEY,
        # Fit-out window. ``fitout_start_date`` / ``fitout_end_date`` are the
        # platform payload's own key names and arrive when an existing SR is
        # hydrated on the approve path; the actual/base pair arrives from
        # ``lease_lookup_node`` on create.
        "fitout_start_date",
        "fitout_end_date",
        "actual_fitout_start_date",
        "actual_fitout_end_date",
        "base_fitout_start_date",
        "base_fitout_end_date",
        # Percentage already recorded on the lease. Display + chip floor only;
        # never sent in a payload. ``fitout_progress`` is the platform DTO's own
        # key, written by the lease lookup; the alias is for callers that
        # hydrate from an SR payload instead.
        "recorded_fitout_progress",
        "fitout_progress",
    }
)

# Fields that must only ever come from a backend lookup, never from the LLM.
BACKEND_ONLY_FIELDS: frozenset[str] = frozenset(
    {
        "lease_id",
        "contract_id",
        "tenant_profile_id",
        "property_id",
        "company_name",
        "brand_name",
        "mall_name",
        "pms_tenant_code",
        "fitout_start_date",
        "fitout_end_date",
        "actual_fitout_start_date",
        "actual_fitout_end_date",
        "base_fitout_start_date",
        "base_fitout_end_date",
        "recorded_fitout_progress",
        "fitout_progress",
    }
)

USER_SUPPLIED_FIELDS: frozenset[str] = (
    ALLOWED_EXTRACTED_FIELDS - BACKEND_DERIVED_FIELDS
)

# Optional — an empty string is an acceptable final value.
OPTIONAL_FIELDS: frozenset[str] = frozenset({COMMENT_FIELD})

# ── Permissions ───────────────────────────────────────────────────────────────
#
# Create is DD_ENGINEER-only on purpose: the platform would accept a create from
# any admin role (it produces a reviewable SR), but the web form hides the
# sub-category from everyone without DD_ENGINEER, and the assistant must not
# offer a path the form refuses. Approve is DD_ENGINEER by the workflow JSON
# (``roles: [12]`` at both levels).

APPROVE_ROLES: frozenset[str] = frozenset({"DD_ENGINEER"})

PERMISSION_MAP: dict[str, tuple[str, ...]] = {
    "DD_ENGINEER": ("CREATE_SR", "APPROVE_SR"),
}

# ── Helper utilities ──────────────────────────────────────────────────────────


def _is_missing(value: object) -> bool:
    return value is None or value == "" or value == []


def get_required_fields(stage_name: str) -> tuple[str, ...]:
    """Return the fields required at *stage_name*; empty for unknown stages."""
    stage = STAGE_REGISTRY.get(stage_name)
    return stage.required_fields if stage else ()


def get_missing_fields(stage_name: str, collected: dict[str, Any]) -> list[str]:
    """Return required fields absent from *collected* for *stage_name*.

    ``lease_brand_mall`` is the lease-lookup trigger: asking for it is what
    makes ``lease_lookup_node`` resolve the rest. On the approve path the SR
    payload may only carry ``lease_code`` / ``lease_id``, so any of the three
    counts — the approver must never be asked to identify a lease they are
    already looking at.
    """
    if stage_name not in STAGE_REGISTRY:
        return []

    missing: list[str] = []

    if stage_name == "APPROVE_SR":
        has_lease = any(
            not _is_missing(collected.get(key)) for key in _LEASE_FIELDS
        )
    else:
        has_lease = not _is_missing(collected.get("lease_brand_mall"))
    if not has_lease:
        missing.append("lease_brand_mall")

    if _is_missing(collected.get(PROGRESS_FIELD)):
        missing.append(PROGRESS_FIELD)
    elif (
        stage_name in STAGES_REQUIRING_EXPLICIT_PROGRESS
        and not collected.get(PROGRESS_CONFIRMED_KEY)
    ):
        # Pre-filled from the stored payload, not answered by this user yet.
        missing.append(PROGRESS_FIELD)

    return missing


def role_can_act_on_stage(role: str, stage_name: str) -> bool:
    """True if *role* is permitted to act on *stage_name*."""
    return stage_name in PERMISSION_MAP.get(role, ())


def role_can_approve(role: str) -> bool:
    """True if *role* may approve a Fitout Progress SR."""
    return role in APPROVE_ROLES


def build_title(pms_tenant_code: str) -> str:
    """Return the auto-generated SR title per the platform convention."""
    code = (pms_tenant_code or "").strip()
    return f"{TITLE_PREFIX} - {code}" if code else TITLE_PREFIX


# ── LLM extraction output shape (Pydantic) ────────────────────────────────────


class ExtractedFieldValue(BaseModel):
    value: str = PydanticField(description="Proposed string value for this field.")
    confidence: float = PydanticField(default=1.0, ge=0.0, le=1.0)


class FitoutProgressExtractedFields(BaseModel):
    """LLM extraction output for the Fitout Progress workflow.

    The validator silently drops anything outside ``ALLOWED_EXTRACTED_FIELDS``
    or inside ``BACKEND_ONLY_FIELDS``, so a hallucinated lease id or fit-out
    date can never reach ``collected_data``.
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
    def _strip_forbidden_and_unknown_fields(self) -> "FitoutProgressExtractedFields":
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
