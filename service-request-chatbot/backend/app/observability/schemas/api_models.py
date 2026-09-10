"""Pydantic v2 response models for the observability read API.

Rules enforced here:
- All JSONB payload fields (input, output, state, diffs, structured_output,
  request_payload, response_payload) are passed through sanitise() so that
  any residual sensitive keys or CoT that slipped into storage are stripped
  before hitting the wire.
- Hidden system prompt fields (prompt_text, system_prompt) are excluded from
  LLMCallResponse entirely.
- model_config = ConfigDict(from_attributes=True) so every model can be built
  directly from an ORM row with model_validate(row).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.observability.redaction import sanitise


class RunTreeNodeResponse(BaseModel):
    """One run/span node, optionally with nested children for tree rendering."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    parent_run_id: UUID | None = None
    run_name: str
    run_type: str
    node_name: str | None = None
    status: str
    error_message: str | None = None
    latency_ms: int | None = None
    started_at: datetime
    completed_at: datetime | None = None
    children: list["RunTreeNodeResponse"] = Field(default_factory=list)


class RunResponse(BaseModel):
    """Flat run record — safe to return as-is (inputs/outputs already sanitised at write time)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trace_id: UUID
    parent_run_id: UUID | None = None
    run_name: str
    run_type: str
    node_name: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    status: str
    error_message: str | None = None
    latency_ms: int | None = None
    started_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _redact_io(self) -> "RunResponse":
        self.input = sanitise(self.input)
        self.output = sanitise(self.output)
        return self


class StateSnapshotResponse(BaseModel):
    """State capture at a node boundary — state payload re-sanitised on read."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trace_id: UUID
    run_id: UUID
    snapshot_type: str
    state: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @model_validator(mode="after")
    def _redact_state(self) -> "StateSnapshotResponse":
        self.state = sanitise(self.state)
        return self


class StateDiffResponse(BaseModel):
    """Delta between successive agent states — diff re-sanitised on read."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trace_id: UUID
    run_id: UUID
    diff: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @model_validator(mode="after")
    def _redact_diff(self) -> "StateDiffResponse":
        self.diff = sanitise(self.diff)
        return self


class LLMCallResponse(BaseModel):
    """LLM call record — system prompt and temperature excluded; structured output re-sanitised."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trace_id: UUID
    run_id: UUID
    provider: str | None = None
    model: str | None = None
    # temperature intentionally omitted — not useful for admin review
    prompt_name: str | None = None
    prompt_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    estimated_cost: Decimal | None = None
    structured_output: dict[str, Any] = Field(default_factory=dict)
    parse_success: bool | None = None
    parse_error: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _redact_output(self) -> "LLMCallResponse":
        self.structured_output = sanitise(self.structured_output)
        return self


class ToolCallResponse(BaseModel):
    """Tool invocation record — request/response payloads re-sanitised on read."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trace_id: UUID
    run_id: UUID
    tool_name: str
    tool_type: str
    request_payload: dict[str, Any] = Field(default_factory=dict)
    response_payload: dict[str, Any] = Field(default_factory=dict)
    status_code: int | None = None
    success: bool | None = None
    latency_ms: int | None = None
    error_message: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _redact_payloads(self) -> "ToolCallResponse":
        self.request_payload = sanitise(self.request_payload)
        self.response_payload = sanitise(self.response_payload)
        return self


class FeedbackResponse(BaseModel):
    """Feedback record submitted against a trace, run, or session."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trace_id: UUID | None = None
    session_id: UUID | None = None
    sr_id: str | None = None
    run_id: UUID | None = None
    user_id: UUID | None = None
    session_id: UUID | None = None
    message_id: UUID | None = None
    source: str | None = None
    sr_id: str | None = None
    feedback_type: str
    score: int | None = None
    label: str | None = None
    comment: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _from_orm_metadata(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "metadata" not in data and "metadata_" in data:
                return {**data, "metadata": data.get("metadata_") or {}}
            return data
        metadata = getattr(data, "metadata_", None)
        if not isinstance(metadata, dict):
            metadata = {}
        return {
            "id": data.id,
            "trace_id": data.trace_id,
            "session_id": getattr(data, "session_id", None),
            "sr_id": getattr(data, "sr_id", None),
            "run_id": data.run_id,
            "user_id": data.user_id,
            "feedback_type": data.feedback_type,
            "score": data.score,
            "label": data.label,
            "comment": data.comment,
            "metadata": metadata,
            "created_at": data.created_at,
        }

    @field_validator("trace_id", "session_id", "run_id", "user_id", mode="before")
    @classmethod
    def _drop_non_uuid(cls, value: Any) -> Any:
        if value is None or isinstance(value, UUID):
            return value
        if isinstance(value, str):
            return value
        return None

    @field_validator("metadata", mode="before")
    @classmethod
    def _coerce_metadata(cls, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}


class TraceResponse(BaseModel):
    """Full trace record — input/output messages included, metadata excluded."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    user_id: UUID
    trace_type: str
    active_agent: str | None = None
    intent: str | None = None
    service_category: str | None = None
    sub_category: str | None = None
    workflow_stage_before: str | None = None
    workflow_stage_after: str | None = None
    input_message: str | None = None
    output_message: str | None = None
    status: str
    error_message: str | None = None
    total_latency_ms: int | None = None
    total_token_count: int | None = None
    estimated_cost: Decimal | None = None
    created_at: datetime
    completed_at: datetime | None = None


class TraceSummaryResponse(BaseModel):
    """Lightweight trace summary for list/pagination responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    user_id: UUID
    trace_type: str
    active_agent: str | None = None
    intent: str | None = None
    status: str
    error_message: str | None = None
    total_latency_ms: int | None = None
    total_token_count: int | None = None
    estimated_cost: Decimal | None = None
    created_at: datetime
    completed_at: datetime | None = None


class TraceDetailResponse(BaseModel):
    """Full trace detail: trace record + all related spans, state captures, and signals."""

    trace: TraceResponse
    runs: list[RunResponse]
    run_tree: list[RunTreeNodeResponse]
    state_snapshots: list[StateSnapshotResponse]
    state_diffs: list[StateDiffResponse]
    llm_calls: list[LLMCallResponse]
    tool_calls: list[ToolCallResponse]
    feedback: list[FeedbackResponse]


class PaginatedTraceListResponse(BaseModel):
    """Paginated trace list with cursor metadata."""

    items: list[TraceSummaryResponse]
    total: int
    page: int
    page_size: int
    has_next: bool


class ReplayTraceEntry(BaseModel):
    """Single trace entry in a session replay."""

    trace: TraceResponse
    runs: list[RunResponse]
    run_tree: list[RunTreeNodeResponse]
    state_snapshots: list[StateSnapshotResponse]
    state_diffs: list[StateDiffResponse]
    llm_calls: list[LLMCallResponse]
    tool_calls: list[ToolCallResponse]
    feedback: list[FeedbackResponse]


class SessionReplayResponse(BaseModel):
    """Ordered replay of all traces within a session, oldest-first."""

    session_id: UUID
    trace_count: int
    traces: list[ReplayTraceEntry]
