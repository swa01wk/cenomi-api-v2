"""Handover Service Request — workflow configuration schema.

This module is the single source of truth for:
- Stage definitions (required fields + required documents per stage)
- Role assignments
- Allowed extractable fields
- Backend-derived (non-extractable) fields
- Document type registries
- Role → stage permission map

Graph nodes **must not** hard-code required fields.  Instead they should
query ``STAGE_REGISTRY[state["workflow_stage"]]``.

LLM extraction output shape lives in ``HandoverExtractedFields`` (Pydantic).
``HandoverExtractionSchema`` is kept for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

from pydantic import BaseModel, Field as PydanticField, field_validator, model_validator

# ── Constants ─────────────────────────────────────────────────────────────────

SERVICE_CATEGORY: Final[str] = "FIT_OUT_AND_HANDOVER"
SUB_CATEGORY: Final[str] = "HANDOVER"
SUB_CATEGORY_HANDED_OVER: Final[str] = "HANDED_OVER"
ASSIGNMENT_TYPE: Final[str] = "workflow"

# ── Literal types ─────────────────────────────────────────────────────────────

WorkflowStage = Literal["CREATE_SR", "FM_REVIEW", "RDD_REVIEW", "CREATE_HANDED_OVER_SR"]

WorkflowRole = Literal["MALL_MANAGER", "FM_MANAGER", "DD_ENGINEER"]

ConfirmationStatus = Literal["PENDING", "CONFIRMED", "REJECTED", "CANCEL_PENDING"]

FMDocumentType = Literal[
    "SR_HANDOVER_CHECKLIST",
    "SR_HANDOVER_SITE_SURVEY",
    "SR_COP_CHECKLIST_OTHER",
]

RDDDocumentType = Literal[
    "DR_SR_HANDOVER_REPORT",
    "SR_REJECTED_HANDOVER_REPORT",
    "SR_HANDOVER_OTHER",
]

HandedOverDocumentType = Literal["SR_HANDOVER_OTHER"]

# ── Stage definition dataclass ────────────────────────────────────────────────


@dataclass(frozen=True)
class StageDefinition:
    """Immutable description of a single workflow stage."""

    stage: str
    role: str
    required_fields: tuple[str, ...]
    required_documents: tuple[str, ...] = field(default_factory=tuple)


# ── Stage instances ───────────────────────────────────────────────────────────

CREATE_SR_STAGE = StageDefinition(
    stage="CREATE_SR",
    role="MALL_MANAGER",
    required_fields=(
        "tenant_profile_id",
        "property_id",
        "lease_code",
        "lease_id",
        "brand_id",
        "mall",
        "brand",
        "lease",          # display label for the lease (same as lease_code; backend-derived)
        "unit_codes",
        "city",
        "contracted_area",
        # Handover Type* — asked first after lease resolution. Value "already_trading"
        # triggers Path B (CREATE_HANDED_OVER_SR); "initiate_handover" continues Path A.
        "already_handovered",
        "title",
        "description",
        "startDate",
        # Asked immediately after startDate (not after endDate): when the user
        # gives a bare date the very next question must be the time for THAT
        # date, otherwise the clock silently defaults and the flow moves on.
        "inspectionTime",  # optional HH:MM — combined with startDate/endDate in payload
        "endDate",
        # Same rule as inspectionTime, for the end of the window: asked right
        # after endDate so a bare end date prompts for its time instead of
        # silently inheriting the start time.
        "inspectionEndTime",  # optional HH:MM
        "inspection_done_by",
        "notes",
        "comments",
    ),
)

FM_REVIEW_STAGE = StageDefinition(
    stage="FM_REVIEW",
    role="FM_MANAGER",
    required_fields=(
        "unit_readiness_date",
        # expected_handover_date is auto-computed as unit_readiness_date + 7 days
        # by merge_state_node. It is NOT asked of the user.
        # comments is in OPTIONAL_FIELDS — asked once but never blocks the flow.
        # Must appear here so _route_after_validation can detect it and ask once.
        "comments",
    ),
    # At least one of these must be uploaded before FM save/approve.
    # Platform roles 20 (FM_MANAGER) and 81 (OPERATIONS): Checklist, Site survey,
    # COP Checklist. Note: SR_HANDOVER_OTHER belongs to Leasing Admin (role 11) only.
    required_documents=(
        "SR_HANDOVER_CHECKLIST",
        "SR_HANDOVER_SITE_SURVEY",
        "SR_COP_CHECKLIST_OTHER",
    ),
)

RDD_REVIEW_STAGE = StageDefinition(
    stage="RDD_REVIEW",
    role="DD_ENGINEER",
    required_fields=(
        # Guidelines first — document upload UI is gated on this.
        "guideLineLink",
        # Documents (Meeting Report OR Rejected mandatory; Other optional) are
        # collected via the document_requirement UI, NOT via a chip-driven
        # rdd_upload_doc_type field. Same UX as FM: one card, all types visible.
        #
        # Outcome of the Handover Meeting Report, asked with Approve /
        # Approve-with-changes chips once that report is uploaded — see the
        # RDD_REVIEW branch in get_missing_fields for when it is suppressed.
        # Listed before the dates so the status is settled first.
        "document_status",
        # trading_date intentionally removed — auto-computed as fitout_end_date + 8 days
        # by merge_state_node. It is NOT asked of the user.
        "actual_handover_date",
        "fitout_start_date",
        "fitout_end_date",
        "comments",
    ),
    # DR_SR_HANDOVER_REPORT is mandatory; SR_REJECTED_HANDOVER_REPORT and
    # SR_HANDOVER_OTHER are optional additional uploads allowed at this stage.
    required_documents=(
        "DR_SR_HANDOVER_REPORT",
        "SR_REJECTED_HANDOVER_REPORT",
        "SR_HANDOVER_OTHER",
    ),
)

CREATE_HANDED_OVER_SR_STAGE = StageDefinition(
    stage="CREATE_HANDED_OVER_SR",
    role="MALL_MANAGER",
    required_fields=(
        "tenant_profile_id",
        "property_id",
        "lease_code",
        "lease_id",
        "brand_id",
        "mall",
        "brand",
        "lease",
        "unit_codes",
        "city",
        "contracted_area",
        "title",
        "actual_shop_handover_date",
        "actual_fitout_start_date",
        "actual_fitout_end_date",
        "actual_trading_date",
        # comments is in OPTIONAL_FIELDS — asked once but never blocks the flow.
        # Must appear here so _route_after_validation can detect it and ask once.
        "comments",
    ),
    required_documents=(),  # SR_HANDOVER_OTHER is optional on Path B (matches platform skeleton)
)

# Ordered date chain for Path B validation (earlier → later).
HANDED_OVER_DATE_CHAIN: tuple[tuple[str, str], ...] = (
    ("actual_shop_handover_date", "actual_fitout_start_date"),
    ("actual_fitout_start_date", "actual_fitout_end_date"),
    ("actual_fitout_end_date", "actual_trading_date"),
)

# ── Registry / ordered stage list ────────────────────────────────────────────

STAGE_REGISTRY: dict[str, StageDefinition] = {
    "CREATE_SR": CREATE_SR_STAGE,
    "FM_REVIEW": FM_REVIEW_STAGE,
    "RDD_REVIEW": RDD_REVIEW_STAGE,
    "CREATE_HANDED_OVER_SR": CREATE_HANDED_OVER_SR_STAGE,
}

WORKFLOW_STAGES: tuple[str, ...] = (
    "CREATE_SR",
    "FM_REVIEW",
    "RDD_REVIEW",
    "CREATE_HANDED_OVER_SR",
)

# ── Derived field sets ────────────────────────────────────────────────────────

# All fields that the LLM / user may supply across every stage.
# document_status now lives in RDD_REVIEW_STAGE.required_fields, so it no longer
# needs to be appended separately.
ALLOWED_EXTRACTED_FIELDS: frozenset[str] = frozenset(
    CREATE_SR_STAGE.required_fields
    + FM_REVIEW_STAGE.required_fields
    + RDD_REVIEW_STAGE.required_fields
)

# Fields populated from backend APIs (lease lookup, tenant profile, etc.).
# The graph must not ask the user for these; they are resolved programmatically.
BACKEND_DERIVED_FIELDS: frozenset[str] = frozenset(
    {
        "tenant_profile_id",
        "property_id",
        "lease_id",
        "brand_id",
        "mall",
        "brand",
        "lease",          # display label set alongside lease_code during lease resolution
        "unit_codes",
        "city",
        "contracted_area",
    }
)

# Fields that the user / LLM must explicitly supply (not backend-derived).
USER_SUPPLIED_FIELDS: frozenset[str] = ALLOWED_EXTRACTED_FIELDS - BACKEND_DERIVED_FIELDS

# Optional fields: present in the stage's required_fields tuple so the bot
# proactively asks for them, but an empty-string answer is accepted as valid
# (the user may legitimately have nothing to add).  These are excluded from the
# blocking-missing check in get_missing_fields once they carry any value
# (including the empty string that merge_state_node normalises "None" to).
#
# ``description`` is included here because users legitimately respond with
# "no description" — the resulting empty string must not be treated as missing
# and must not block title auto-generation or the confirmation card.
OPTIONAL_FIELDS: frozenset[str] = frozenset(
    {"description", "comments", "notes", "inspectionTime", "inspectionEndTime", "title"}
)

# ── Short flow (HANDOVER_SHORT_FLOW_ENABLED) ─────────────────────────────────
# The three inspection fields are asked as ONE question instead of three
# consecutive turns.  Members keep their own keys, validation rules and payload
# mapping — only the *ask* is merged, and a partial answer re-asks whatever is
# still open (see ``missing_field_node._build_inspection_group_ask``).
# Path B (Already Trading) records what already happened, so all four dates
# come from the user — there is nothing to suggest and nothing to derive.
HANDED_OVER_FIELD_GROUP: tuple[str, ...] = (
    "actual_shop_handover_date",
    "actual_fitout_start_date",
    "actual_fitout_end_date",
    "actual_trading_date",
)

# RDD review asks for the meeting outcome and the milestone dates together.
# ``trading_date`` is absent on purpose: merge_state derives it from the
# fit-out end date, so it is shown but never asked.
RDD_REVIEW_FIELD_GROUP: tuple[str, ...] = (
    "document_status",
    "actual_handover_date",
    "fitout_start_date",
    "fitout_end_date",
)

INSPECTION_FIELD_GROUP: tuple[str, ...] = (
    "startDate",
    "endDate",
    "inspection_done_by",
)

# Fields the short flow fills on the user's behalf (deterministic template,
# optionally LLM-polished) instead of asking for them.  They stay in
# ``required_fields`` so validation and the payload builder still see real
# values; only the question loop skips them, and every one of them remains
# editable on the review card.
AI_FILLED_FIELDS: frozenset[str] = frozenset(
    {"title", "description", "notes", "comments"}
)

# Inspection clock times. The short flow keeps them collectable — a user who
# types "6 Sep 2pm" still gets 14:00 — but never spends a turn asking for them;
# the payload builder defaults an unanswered time to 09:00 as it always has.
_SHORT_FLOW_UNASKED_TIME_FIELDS: frozenset[str] = frozenset(
    {"inspectionTime", "inspectionEndTime"}
)

# Everything the short flow declines to ask for: generated text plus the times.
SHORT_FLOW_UNASKED_FIELDS: frozenset[str] = (
    AI_FILLED_FIELDS | _SHORT_FLOW_UNASKED_TIME_FIELDS
)

# Stages where the short flow applies. FM_REVIEW / RDD_REVIEW ask their own
# comments question and are untouched.
AI_FILLED_STAGES: frozenset[str] = frozenset({"CREATE_SR", "CREATE_HANDED_OVER_SR"})


def short_flow_enabled() -> bool:
    """True when the four-turn CREATE flow is switched on.

    Read through ``get_settings()`` (cached) at call time rather than captured
    at import, so tests can toggle the flag with the usual settings override.
    """
    from app.core.config import get_settings

    return bool(get_settings().handover_short_flow_enabled)


def ai_filled_fields_for(stage_name: str) -> frozenset[str]:
    """Text fields the short flow generates rather than asks for."""
    if not short_flow_enabled() or stage_name not in AI_FILLED_STAGES:
        return frozenset()
    return AI_FILLED_FIELDS


def unasked_fields_for(stage_name: str) -> frozenset[str]:
    """Every field the short flow skips in the question loop for *stage_name*."""
    if not short_flow_enabled() or stage_name not in AI_FILLED_STAGES:
        return frozenset()
    return SHORT_FLOW_UNASKED_FIELDS
# ``title`` is optional — merge_state_node / payload_builder auto-generate a
# default ("handover-{lease}-{slug}" for Path A, "Already Trading - {lease}"
# for Path B) when the user skips.

# ── Document type registries ──────────────────────────────────────────────────

# All document types accepted at the FM_REVIEW stage (includes optional "Other").
FM_ALLOWED_DOCUMENTS: tuple[str, ...] = FM_REVIEW_STAGE.required_documents

# All document types accepted at the RDD_REVIEW stage.
RDD_ALLOWED_DOCUMENTS: tuple[str, ...] = RDD_REVIEW_STAGE.required_documents

# Either Handover Meeting Report or Rejected Report satisfies RDD submission.
# (Doc-count validation accepts any doc whose type is in this tuple.)
RDD_REQUIRED_DOCUMENTS: tuple[str, ...] = (
    "DR_SR_HANDOVER_REPORT",
    "SR_REJECTED_HANDOVER_REPORT",
)

# All document types accepted at the CREATE_HANDED_OVER_SR stage (all optional).
HANDED_OVER_ALLOWED_DOCUMENTS: tuple[str, ...] = ("SR_HANDOVER_OTHER",)

# RDD upload type codes (collected_data.rdd_upload_doc_type).
RDD_DOC_TYPE_MEETING = "DR_SR_HANDOVER_REPORT"
RDD_DOC_TYPE_REJECTED = "SR_REJECTED_HANDOVER_REPORT"
RDD_DOC_TYPE_OTHER = "SR_HANDOVER_OTHER"
RDD_UPLOAD_DOC_TYPES: frozenset[str] = frozenset(
    {RDD_DOC_TYPE_MEETING, RDD_DOC_TYPE_REJECTED, RDD_DOC_TYPE_OTHER}
)

# Meeting Report statuses vs Rejected (auto DISAPPROVED).
RDD_MEETING_STATUSES: frozenset[str] = frozenset({"APPROVED", "APPROVED_WITH_CHANGES"})
RDD_REJECTED_STATUS = "DISAPPROVED"

ALL_DOCUMENT_TYPES: frozenset[str] = frozenset(
    FM_ALLOWED_DOCUMENTS + RDD_ALLOWED_DOCUMENTS + HANDED_OVER_ALLOWED_DOCUMENTS
)

# ── Auto-computed (backend-computed) field set ────────────────────────────────
# Fields whose values are derived from other collected data by merge_state_node.
# These are never asked of the user directly.
# Re-exported here so that tests and services can import from the schema module.
from app.agents.graph.nodes.shared.merge_state_node import BACKEND_COMPUTED_FIELDS  # noqa: E402

# ── Permission map: role → stages the role may act on ────────────────────────

PERMISSION_MAP: dict[str, tuple[str, ...]] = {
    stage_def.role: (stage_def.stage,) for stage_def in STAGE_REGISTRY.values()
}
# OPERATIONS shares FM_REVIEW permissions with FM_MANAGER but is not a
# stage_def.role (there is only one role per StageDefinition), so it is
# added explicitly here.
PERMISSION_MAP["OPERATIONS"] = ("FM_REVIEW",)
# MALL_MANAGER can act on both CREATE_SR and CREATE_HANDED_OVER_SR.
PERMISSION_MAP["MALL_MANAGER"] = ("CREATE_SR", "CREATE_HANDED_OVER_SR")

# ── Helper utilities ──────────────────────────────────────────────────────────


def get_stage(stage_name: str) -> StageDefinition:
    """Return a ``StageDefinition`` or raise ``KeyError`` for unknown stages."""
    try:
        return STAGE_REGISTRY[stage_name]
    except KeyError:
        valid = ", ".join(WORKFLOW_STAGES)
        raise KeyError(f"Unknown workflow stage '{stage_name}'. Valid: {valid}") from None


def get_required_fields(stage_name: str) -> tuple[str, ...]:
    """Return required fields for *stage_name* — single point of truth for nodes."""
    return get_stage(stage_name).required_fields


def get_required_documents(stage_name: str) -> tuple[str, ...]:
    """Return required document types for *stage_name*."""
    return get_stage(stage_name).required_documents


def get_missing_fields(stage_name: str, collected: dict) -> list[str]:
    """Return required fields absent from *collected* for *stage_name*.

    A field is considered missing when its value is ``None``, ``""``, or
    ``[]``.  Integer ``0`` and boolean ``False`` are treated as valid values,
    consistent with ``validate_required_fields`` in ``validation_service``.

    Optional fields (``OPTIONAL_FIELDS``) are excluded from the missing list
    once the user has explicitly answered them — even with an empty string
    (which ``merge_state_node`` writes when the user declines, e.g. "no
    comments").  A ``None`` value for an optional field still counts as
    missing so the bot still asks proactively on the first pass.

    ``document_status`` is a plain RDD_REVIEW required field. It does not need a
    condition here because the upload state already gates it on both sides:
    ``missing_field_node`` returns the document_requirement card before any
    user_missing field while the mandatory report is still absent, and once a
    *Rejected* report is uploaded ``merge_state_node`` has already written
    ``DISAPPROVED``, so it is no longer missing. What remains — a Handover
    Meeting Report uploaded with no status picked — is exactly the case the
    Approve / Approve-with-changes chips are for.
    """

    def _is_missing(value: object) -> bool:
        return value is None or value == "" or value == []

    def _is_missing_for_field(field_name: str, value: object) -> bool:
        if field_name in OPTIONAL_FIELDS:
            # Only missing when the field has never been touched (value is None).
            # An empty string means the user explicitly declined → not missing.
            return value is None
        return _is_missing(value)

    # Short flow: fields the system fills are never "missing" from the user's
    # point of view — autofill writes the text ones once the inspection group
    # resolves, times default in the payload builder, and the review card is
    # where all of them are checked and edited.
    unasked = unasked_fields_for(stage_name)

    missing = [
        f
        for f in get_required_fields(stage_name)
        if f not in unasked and _is_missing_for_field(f, collected.get(f))
    ]

    return missing


def role_can_act_on_stage(role: str, stage_name: str) -> bool:
    """True if *role* is permitted to act on *stage_name*."""
    return stage_name in PERMISSION_MAP.get(role, ())


# ── LLM extraction output shape (Pydantic) — legacy ──────────────────────────


class HandoverExtractionSchema(BaseModel):
    """Shape for LLM JSON output — values are proposals until validated in code.

    Kept for backward compatibility.  New code should use
    ``HandoverExtractedFields`` which includes per-field confidence scores.
    """

    summary: str | None = PydanticField(
        default=None,
        description="Short summary of user intent",
    )
    fields: dict[str, str] = PydanticField(
        default_factory=dict,
        description="Proposed field_key → string value (keys must be in ALLOWED_EXTRACTED_FIELDS)",
    )


# ── Field sets for extraction ─────────────────────────────────────────────────

# Fields that the LLM may propose — user-supplied hints that the graph can
# accept and later merge into collected_data.
EXTRACTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "lease_code",
        "mall",
        "brand",
        # Tenant / company name — maps to platform GET /leases?company=
        "company",
        # "title" is extractable: the user may provide their own title.
        # If not provided, merge_state_node auto-generates it.
        "title",
        "description",
        "startDate",
        "endDate",
        "inspectionTime",   # HH:MM (24-hour); combined with startDate/endDate in payload
        "inspection_done_by",
        "comments",
        "notes",
        "unit_readiness_date",
        "expected_handover_date",
        "guideLineLink",
        "document_status",    # APPROVED | APPROVED_WITH_CHANGES | DISAPPROVED
        "rdd_upload_doc_type",  # DR_SR_HANDOVER_REPORT | SR_REJECTED_* | SR_HANDOVER_OTHER
        "actual_handover_date",
        "fitout_start_date",
        "fitout_end_date",
        # trading_date intentionally excluded — auto-computed from fitout_end_date + 8 days.
        # The LLM must never extract or propose this field.
        # ── Path B (HANDED_OVER / Already Trading) fields ─────────────────────
        "already_handovered",        # already_trading | initiate_handover discriminator
        "actual_shop_handover_date",
        "actual_fitout_start_date",
        "actual_fitout_end_date",
        "actual_trading_date",
        # ── Fitout Confirmation workflow fields ────────────────────────────────
        # Radio fields (yes/no) for each sub-category
        "fitout_start_done",
        "fitout_complete_done",
        "trading_done",
        # Actual date fields
        "actual_fitout_complete_date",
        # Planned date fields
        "planned_fitout_start_date",
        "planned_fitout_end_date",
        "planned_fitout_complete_date",
        "planned_trading_date",
        # Optional shared field
        "delay_reason",
    }
)

# Fields that must only ever come from backend API lookups.
# The LLM must never populate these; any attempt is silently stripped.
BACKEND_ONLY_FIELDS: frozenset[str] = frozenset(
    {
        "tenant_profile_id",
        "property_id",
        "brand_id",
        "lease_id",
        "contract_id",
        "unit_codes",
        "city",
        "contracted_area",
    }
)

InspectionDoneBy = Literal["FM_MANAGER", "OPERATIONS"]


# ── Richer extraction output shape (Pydantic) ─────────────────────────────────


class ExtractedFieldValue(BaseModel):
    """A single extracted field with its proposed value and extraction confidence."""

    value: str = PydanticField(description="Proposed string value for this field.")
    confidence: float = PydanticField(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="LLM confidence that this value is correct (0.0–1.0).",
    )


class HandoverExtractedFields(BaseModel):
    """Rich LLM extraction output: per-field confidence, auto-stripping of forbidden keys.

    The ``model_validator`` silently removes any field that is not in
    ``EXTRACTABLE_FIELDS`` or is present in ``BACKEND_ONLY_FIELDS`` so that
    a hallucinating model can never inject backend-derived IDs into the state.
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
    def _strip_forbidden_and_unknown_fields(self) -> "HandoverExtractedFields":
        """Remove keys that are not explicitly extractable or are backend-only."""
        self.fields = {
            k: v
            for k, v in self.fields.items()
            if k in EXTRACTABLE_FIELDS and k not in BACKEND_ONLY_FIELDS
        }
        return self

    def to_flat_dict(self) -> dict[str, str]:
        """Return ``{field_name: value}`` without confidence scores."""
        return {k: v.value for k, v in self.fields.items()}

    def to_confidence_dict(self) -> dict[str, float]:
        """Return ``{field_name: confidence}`` scores only."""
        return {k: v.confidence for k, v in self.fields.items()}

    def to_state_dict(self) -> dict[str, dict[str, Any]]:
        """Return the full ``{field: {value, confidence}}`` shape for state storage."""
        return {k: {"value": v.value, "confidence": v.confidence} for k, v in self.fields.items()}


# ── Trace metadata dataclass ──────────────────────────────────────────────────


@dataclass
class ExtractionTraceMeta:
    """Metadata captured during a single FieldExtractionService.extract() call."""

    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    parse_success: bool = False
    parse_error: str | None = None
    retry_count: int = 0
    raw_output: dict[str, Any] | None = None
