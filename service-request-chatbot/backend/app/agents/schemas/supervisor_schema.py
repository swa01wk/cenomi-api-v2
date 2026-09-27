"""Pydantic model for the Supervisor intent-classification output.

``SupervisorDecision`` is returned by the LLM and consumed exclusively by
``supervisor_node``.  Downstream agents and form-field nodes must not import
or depend on this model directly.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SupervisorDecision(BaseModel):
    """Structured JSON output from the supervisor intent-classification LLM call.

    The model is used only for routing — it must never carry form-field data,
    API credentials, or submission payloads.
    """

    intent: Literal[
        # ── Generic ──────────────────────────────────────────────────────────
        "ASK_HELP",
        "CHECK_SERVICE_REQUEST_STATUS",
        "PREVIEW_SERVICE_REQUEST",
        "UNKNOWN",
        # ── RDD / Fit-out ─────────────────────────────────────────────────────
        "CREATE_RDD_SERVICE_REQUEST",
        "CREATE_HANDED_OVER_SR",
        "UPDATE_RDD_SERVICE_REQUEST",
        "APPROVE_RDD_SERVICE_REQUEST",
        "CREATE_FITOUT_CONFIRM_FOS",
        "CREATE_FITOUT_CONFIRM_END",
        "CREATE_FITOUT_CONFIRM_TRADING",
        "APPROVE_FITOUT_CONFIRM_FOS",
        "APPROVE_FITOUT_CONFIRM_END",
        "APPROVE_FITOUT_CONFIRM_TRADING",
        "CREATE_FITOUT_PROGRESS",
        "APPROVE_FITOUT_PROGRESS",
        # ── Work Permit ───────────────────────────────────────────────────────
        "CREATE_WORK_PERMIT_SR",
        "APPROVE_WORK_PERMIT_SR",
        # ── Sales Data ────────────────────────────────────────────────────────
        "UPLOAD_SALES_DATA",
        "ENTER_SALES_DATA_MANUAL",
        "CHECK_SALES_DATA_STATUS",
        "UPLOAD_SUPPORTING_DOCUMENT",
        "REVIEW_SALES_DATA",
        "LIST_NOTIFICATIONS",
        "GET_NOTIFICATION_DETAIL",
        "MARK_NOTIFICATION_READ",
        "OPEN_NOTIFICATION",
        "APPROVE_SALES_DATA",
    ] = Field(description="Classified user intent")

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Classification confidence in [0, 1]; below threshold triggers clarification",
    )

    service_category: Optional[str] = Field(
        default=None,
        description="Top-level service category (e.g. FIT_OUT_AND_HANDOVER)",
    )

    sub_category: Optional[str] = Field(
        default=None,
        description="Sub-category within service_category (e.g. HANDOVER)",
    )

    target_agent: Optional[str] = Field(
        default=None,
        description="Name of the downstream agent that should handle this request",
    )

    reasoning: str = Field(
        description="Brief explanation of the classification decision (logged, not shown to user)",
    )
