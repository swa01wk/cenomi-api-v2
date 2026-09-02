"""Metrics endpoint for admin dashboards — computed live from the database."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.db.models import AgentFeedback, AgentLLMCall, AgentTrace
from app.db.session import DbSession

router = APIRouter(prefix="/observability")


@router.get("/metrics/summary")
async def metrics_summary(db: DbSession) -> dict[str, Any]:
    """Return aggregate observability metrics computed from agent_traces and agent_llm_calls."""

    # --- trace-level aggregates (single pass) ---
    trace_result = await db.execute(
        select(
            func.count(AgentTrace.id).label("total_traces"),
            func.count(AgentTrace.id).filter(AgentTrace.status == "SUCCESS").label("success_count"),
            func.count(AgentTrace.id).filter(AgentTrace.status == "FAILED").label("failed_traces"),
            func.avg(AgentTrace.total_latency_ms).label("avg_latency_ms"),
        )
    )
    t = trace_result.one()
    total_traces: int = t.total_traces or 0
    success_count: int = t.success_count or 0
    failed_traces: int = t.failed_traces or 0
    avg_latency_ms: float | None = float(t.avg_latency_ms) if t.avg_latency_ms is not None else None
    success_rate: float = (success_count / total_traces) if total_traces > 0 else 0.0

    # --- LLM-call token / cost aggregates ---
    llm_result = await db.execute(
        select(
            func.coalesce(func.sum(AgentLLMCall.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(AgentLLMCall.estimated_cost), 0).label("total_cost"),
        )
    )
    llm_row = llm_result.one()
    total_tokens: int = int(llm_row.total_tokens)
    total_cost: float = float(llm_row.total_cost)

    # --- answer-status aggregates (007) ---
    answer_result = await db.execute(
        select(
            func.count(AgentTrace.id)
            .filter(AgentTrace.answer_status == "ANSWERED")
            .label("answered"),
            func.count(AgentTrace.id)
            .filter(AgentTrace.answer_status == "PARTIAL")
            .label("partial"),
            func.count(AgentTrace.id)
            .filter(AgentTrace.answer_status == "UNANSWERED")
            .label("unanswered"),
            func.count(AgentTrace.id)
            .filter(AgentTrace.answer_status.is_not(None))
            .label("classified"),
        )
    )
    a = answer_result.one()
    classified: int = a.classified or 0
    answered_rate: float = (a.answered / classified) if classified > 0 else 0.0

    # --- end-user rating aggregates (007) ---
    rating_result = await db.execute(
        select(
            func.count(AgentFeedback.id).label("ratings_count"),
            func.avg(AgentFeedback.score).label("avg_rating"),
        ).where(AgentFeedback.feedback_type == "USER_RATING")
    )
    r = rating_result.one()
    ratings_count: int = r.ratings_count or 0
    avg_rating: float | None = float(r.avg_rating) if r.avg_rating is not None else None

    return {
        "total_traces": total_traces,
        "success_rate": success_rate,
        "failed_traces": failed_traces,
        "avg_latency_ms": avg_latency_ms if avg_latency_ms is not None else 0,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        # Additive fields — answered/unanswered classification + CSAT.
        "answered_traces": a.answered or 0,
        "partial_traces": a.partial or 0,
        "unanswered_traces": a.unanswered or 0,
        "answered_rate": answered_rate,
        "ratings_count": ratings_count,
        "avg_rating": avg_rating,
    }
