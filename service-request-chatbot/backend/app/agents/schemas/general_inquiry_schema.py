"""General Inquiry SR — workflow configuration schema.

Single-stage workflow: CREATE_SR only.  No multi-phase review.
Approval is handled entirely by the platform (Customer Relations Team, level 1).
Auto-approved if a Customer Relations Team user creates it themselves.

Fields mirror the platform form skeleton (``1730109769_update_workflow_sr_gen_inq``):
  title, description, notes (optional), file/documents_ids (optional).
Lease resolution follows the same backend-derived pattern as handover.

Document type: ``SR_GEN_INQ`` — optional at create and resubmit.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Final, Literal

from pydantic import BaseModel, Field

# ── Constants ────────────────────────────────────────────────────────────────

SERVICE_CATEGORY: Final[str] = "INQUIRY"
SUB_CATEGORY: Final[str] = "GENERAL"

# ── Stage definition ─────────────────────────────────────────────────────────

WorkflowStage = Literal["CREATE_SR"]


@dataclass(frozen=True)
class StageDefinition:
    """Immutable description of a single workflow stage."""

    stage: str
    role: str
    required_fields: tuple[str, ...]
    required_documents: tuple[str, ...] = dc_field(default_factory=tuple)


CREATE_SR_STAGE = StageDefinition(
    stage="CREATE_SR",
    role="ANY",  # both tenant and admin can create
    required_fields=(
        # Backend-derived (from lease lookup)
        "tenant_profile_id",
        "property_id",
        "lease_code",
        "lease_id",
        "brand_id",
        "mall",
        "brand",
        "lease",
        "lease_brand_mall",
        "contract_id",
        "unit_codes",
        "city",
        # User-provided
        "title",
        "description",
    ),
)

STAGE_REGISTRY: dict[str, StageDefinition] = {
    "CREATE_SR": CREATE_SR_STAGE,
}

# Fields the user can type (LLM extracts these)
EXTRACTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "notes",
        "lease_code",
        "company",
        "lease_brand_mall",
    }
)

# Resolved by lease_lookup, never asked directly
BACKEND_FIELDS: frozenset[str] = frozenset(
    {
        "tenant_profile_id",
        "property_id",
        "brand_id",
        "lease_id",
        "contract_id",
        "unit_codes",
        "city",
        "lease_brand_mall",
        "mall",
        "brand",
        "lease",
    }
)

AUTO_GENERATED_FIELDS: frozenset[str] = frozenset()

OPTIONAL_FIELDS: frozenset[str] = frozenset({"notes"})

# ── Extraction model ─────────────────────────────────────────────────────────


class GeneralInquiryExtractedFields(BaseModel):
    """Pydantic model for LLM field extraction output."""

    title: str | None = Field(default=None, description="Short title for the inquiry")
    description: str | None = Field(
        default=None, description="Detailed inquiry text"
    )
    notes: str | None = Field(default=None, description="Optional notes")
    lease_code: str | None = Field(
        default=None, description="Lease code (e.g. t0105712)"
    )
    company: str | None = Field(default=None, description="Company/tenant name")
    lease_brand_mall: str | None = Field(
        default=None, description="Lease-Brand-Mall combo"
    )


# ── Missing fields helper ────────────────────────────────────────────────────


def get_missing_fields(stage: str, collected: dict[str, Any]) -> list[str]:
    """Return field names still missing for the given stage."""
    stage_def = STAGE_REGISTRY.get(stage)
    if not stage_def:
        return []
    return [f for f in stage_def.required_fields if not collected.get(f)]
