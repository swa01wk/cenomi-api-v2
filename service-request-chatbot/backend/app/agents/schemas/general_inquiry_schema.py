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

import re
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
        # Backend-derived (from lease lookup). Title and description are
        # written from the selected lease and brand, not asked.
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

AUTO_GENERATED_FIELDS: frozenset[str] = frozenset({"title", "description"})

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


# Seed the lease picker sends. It names the lease, not the inquiry.
_GI_SEED_RE = re.compile(
    r"^i want to create a general inquiry service request\b",
    re.IGNORECASE,
)
_GI_TOPIC_RE = re.compile(
    r"\b(about|regarding|because|issue|problem|question)\b|\?",
    re.IGNORECASE,
)
_GI_CONTROL_RE = re.compile(
    r"^(guide me|leave the form open|yes|no|ok|okay|sure|yep|yeah|yup)\.?$",
    re.IGNORECASE,
)


def title_and_description_from_selection(
    collected: dict[str, Any],
) -> tuple[str, str] | None:
    """Subject and description taken from the lease the user picked.

    Nothing else is asked. Brand, mall and lease code are enough.
    """
    brand = str(collected.get("brand") or "").strip()
    mall = str(collected.get("mall") or "").strip()
    lease = str(collected.get("lease_code") or collected.get("lease") or "").strip()
    if not brand and not lease:
        return None
    subject = brand or lease
    title_parts = ["General Inquiry", subject]
    if mall:
        title_parts.append(mall)
    title = " - ".join(title_parts)
    if brand and mall and lease:
        description = f"General Inquiry for {brand} at {mall}, lease {lease}."
    elif brand and mall:
        description = f"General Inquiry for {brand} at {mall}."
    elif brand and lease:
        description = f"General Inquiry for {brand}, lease {lease}."
    elif brand:
        description = f"General Inquiry for {brand}."
    else:
        description = f"General Inquiry for lease {lease}."
    return title, description


def apply_lease_title_and_description(collected: dict[str, Any]) -> dict[str, Any]:
    """Fill an empty title and description from the selected lease."""
    generated = title_and_description_from_selection(collected)
    if not generated:
        return collected
    title, description = generated
    if not str(collected.get("title") or "").strip():
        collected["title"] = title
    if not str(collected.get("description") or "").strip():
        collected["description"] = description
    return collected


def infer_title_and_description(messages: list[str]) -> tuple[str, str] | None:
    """Title and description from the latest message that states the inquiry.

    The user types the intent once. That text is both the subject and the
    description. Guided-tour answers and the plain "create a General Inquiry"
    seed have no topic to copy, so they are skipped. A later real message
    wins over an earlier one.
    """
    chosen = ""
    for raw in messages:
        text = " ".join(str(raw or "").split())
        if not text or _GI_CONTROL_RE.match(text):
            continue
        if _GI_SEED_RE.match(text) and not _GI_TOPIC_RE.search(text):
            continue
        if re.fullmatch(r"t\d+", text, flags=re.IGNORECASE):
            continue
        chosen = text
    if not chosen:
        return None
    title = " ".join(chosen.split()[:12]).rstrip(".,;:")
    return title, chosen
