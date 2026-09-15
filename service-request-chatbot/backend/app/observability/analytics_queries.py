"""Live SQL aggregations for the AI-success metrics APIs (ticket 3713)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.db.models import (
    AgentFeedback,
    AgentLLMCall,
    AgentTrace,
    AnalyticsEvent,
    ChatSession,
    ServiceRequestDraft,
)
from app.observability.analytics_constants import (
    CREATE_SR_INTENTS,
    END_REASON_IDLE,
    EVENT_SR_LIFECYCLE_COMPLETED,
    EVENT_SR_SUBMITTED,
    FEEDBACK_CSAT,
    FEEDBACK_FLOW_USEFUL,
    FEEDBACK_TURN_THUMBS,
    LABEL_NOT_USEFUL,
    LABEL_THUMBS_DOWN,
    LABEL_THUMBS_UP,
    LABEL_USEFUL,
    TERMINAL_WORKFLOW_STAGES,
    ratio,
)

_PREVIEW_OR_LATER_STAGES: frozenset[str] = frozenset(
    {
        "CREATE_SR",
        "FM_REVIEW",
        "RDD_REVIEW",
        "SR_CREATED",
        *TERMINAL_WORKFLOW_STAGES,
    }
)


def parse_window(
    from_ts: datetime | None, to_ts: datetime | None
) -> tuple[datetime, datetime]:
    """Default window: last 7 days ending now (UTC)."""
    end = to_ts or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = from_ts or (end - timedelta(days=7))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return start, end


def _trace_filters(
    start: datetime,
    end: datetime,
    agent: str | None,
    user_id: UUID | None,
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [
        AgentTrace.created_at >= start,
        AgentTrace.created_at < end,
    ]
    if agent:
        filters.append(AgentTrace.active_agent == agent)
    if user_id:
        filters.append(AgentTrace.user_id == user_id)
    return filters


def _session_filters(
    start: datetime,
    end: datetime,
    user_id: UUID | None,
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [
        ChatSession.created_at >= start,
        ChatSession.created_at < end,
    ]
    if user_id:
        filters.append(ChatSession.user_id == user_id)
    return filters


async def _scalar(db: AsyncSession, stmt: Any, default: Any = 0) -> Any:
    result = await db.execute(stmt)
    value = result.scalar_one_or_none()
    return default if value is None else value


async def compute_summary(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    agent: str | None = None,
    user_id: UUID | None = None,
) -> dict[str, Any]:
    t_filters = _trace_filters(start, end, agent, user_id)
    s_filters = _session_filters(start, end, user_id)

    trace_row = (
        await db.execute(
            select(
                func.count(AgentTrace.id).label("total_traces"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.status == "SUCCESS")
                .label("success_count"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.status == "FAILED")
                .label("failed_traces"),
                func.avg(AgentTrace.total_latency_ms).label("avg_latency_ms"),
            ).where(*t_filters)
        )
    ).one()
    total_traces = int(trace_row.total_traces or 0)
    success_count = int(trace_row.success_count or 0)
    failed_traces = int(trace_row.failed_traces or 0)
    avg_latency_ms = float(trace_row.avg_latency_ms or 0)

    llm_stmt = (
        select(
            func.coalesce(func.sum(AgentLLMCall.total_tokens), 0),
            func.coalesce(func.sum(AgentLLMCall.estimated_cost), 0),
        )
        .select_from(AgentLLMCall)
        .join(AgentTrace, AgentTrace.id == AgentLLMCall.trace_id)
        .where(*t_filters)
    )
    llm_row = (await db.execute(llm_stmt)).one()
    total_tokens = int(llm_row[0] or 0)
    total_cost = float(llm_row[1] or 0)

    answer_row = (
        await db.execute(
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
            ).where(*t_filters)
        )
    ).one()
    classified = int(answer_row.classified or 0)
    answered_rate = (answer_row.answered / classified) if classified > 0 else 0.0

    rating_row = (
        await db.execute(
            select(
                func.count(AgentFeedback.id).label("ratings_count"),
                func.avg(AgentFeedback.score).label("avg_rating"),
            ).where(
                AgentFeedback.feedback_type == "USER_RATING",
                AgentFeedback.created_at >= start,
                AgentFeedback.created_at < end,
            )
        )
    ).one()
    ratings_count = int(rating_row.ratings_count or 0)
    avg_rating = (
        float(rating_row.avg_rating) if rating_row.avg_rating is not None else None
    )

    sessions = int(await _scalar(db, select(func.count(ChatSession.id)).where(*s_filters)))
    ended_sessions = int(
        await _scalar(
            db,
            select(func.count(ChatSession.id)).where(
                *s_filters, ChatSession.ended_at.is_not(None)
            ),
        )
    )
    idle_abandoned = int(
        await _scalar(
            db,
            select(func.count(ChatSession.id)).where(
                *s_filters, ChatSession.end_reason == END_REASON_IDLE
            ),
        )
    )
    avg_wall = await _scalar(
        db,
        select(
            func.avg(
                func.extract(
                    "epoch",
                    func.coalesce(ChatSession.ended_at, ChatSession.last_activity_at)
                    - ChatSession.created_at,
                )
                * 1000
            )
        ).where(*s_filters),
        default=0,
    )
    avg_active = await _scalar(
        db,
        select(func.avg(ChatSession.client_active_ms)).where(*s_filters),
        default=0,
    )
    avg_agent = await _scalar(
        db,
        select(func.avg(AgentTrace.total_latency_ms)).where(*t_filters),
        default=0,
    )

    p95 = await _scalar(
        db,
        select(func.percentile_cont(0.95).within_group(AgentTrace.total_latency_ms)).where(
            *t_filters, AgentTrace.total_latency_ms.is_not(None)
        ),
        default=0,
    )

    create_intent = int(
        await _scalar(
            db,
            select(func.count(distinct(AgentTrace.session_id))).where(
                *t_filters, AgentTrace.intent.in_(CREATE_SR_INTENTS)
            ),
        )
    )
    sr_submitted = await _count_sr_submitted(db, start, end, user_id, agent)
    useful = int(
        await _scalar(
            db,
            select(func.count(AgentFeedback.id)).where(
                AgentFeedback.created_at >= start,
                AgentFeedback.created_at < end,
                AgentFeedback.feedback_type == FEEDBACK_FLOW_USEFUL,
                AgentFeedback.label == LABEL_USEFUL,
            ),
        )
    )
    useful_total = int(
        await _scalar(
            db,
            select(func.count(AgentFeedback.id)).where(
                AgentFeedback.created_at >= start,
                AgentFeedback.created_at < end,
                AgentFeedback.feedback_type == FEEDBACK_FLOW_USEFUL,
            ),
        )
    )
    useful_sessions = int(
        await _scalar(
            db,
            select(func.count(distinct(AgentFeedback.session_id))).where(
                AgentFeedback.created_at >= start,
                AgentFeedback.created_at < end,
                AgentFeedback.feedback_type == FEEDBACK_FLOW_USEFUL,
                AgentFeedback.session_id.is_not(None),
            ),
        )
    )

    submitted_cost = 0.0
    if sr_submitted:
        submitted_sessions = select(AnalyticsEvent.session_id).where(
            AnalyticsEvent.event_name == EVENT_SR_SUBMITTED,
            AnalyticsEvent.occurred_at >= start,
            AnalyticsEvent.occurred_at < end,
        )
        cost_val = await _scalar(
            db,
            select(func.coalesce(func.sum(AgentTrace.estimated_cost), 0)).where(
                AgentTrace.session_id.in_(submitted_sessions),
                *t_filters,
            ),
            default=0,
        )
        submitted_cost = float(cost_val or 0)

    return {
        "total_traces": total_traces,
        "success_rate": ratio(success_count, total_traces),
        "failed_traces": failed_traces,
        "avg_latency_ms": avg_latency_ms,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "answered_traces": int(answer_row.answered or 0),
        "partial_traces": int(answer_row.partial or 0),
        "unanswered_traces": int(answer_row.unanswered or 0),
        "answered_rate": answered_rate,
        "ratings_count": ratings_count,
        "avg_rating": avg_rating,
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "sessions": sessions,
        "ended_sessions": ended_sessions,
        "idle_abandoned": idle_abandoned,
        "avg_wall_session_ms": float(avg_wall or 0),
        "avg_client_active_ms": float(avg_active or 0),
        "avg_agent_time_ms": float(avg_agent or 0),
        "sr_submitted": sr_submitted,
        "sr_completion_rate": ratio(sr_submitted, create_intent),
        "usefulness_rate": ratio(useful, useful_total),
        "feedback_coverage": ratio(useful_sessions, sr_submitted),
        "cost_per_successful_sr": (
            submitted_cost / sr_submitted if sr_submitted else 0.0
        ),
        "p95_latency_ms": float(p95 or 0),
    }


async def _count_sr_submitted(
    db: AsyncSession,
    start: datetime,
    end: datetime,
    user_id: UUID | None,
    agent: str | None,
) -> int:
    event_filters: list[Any] = [
        AnalyticsEvent.event_name == EVENT_SR_SUBMITTED,
        AnalyticsEvent.occurred_at >= start,
        AnalyticsEvent.occurred_at < end,
    ]
    if user_id:
        event_filters.append(AnalyticsEvent.user_id == user_id)
    from_events = int(
        await _scalar(
            db, select(func.count(distinct(AnalyticsEvent.session_id))).where(*event_filters)
        )
    )
    if from_events:
        return from_events
    draft_filters: list[Any] = [
        ServiceRequestDraft.sr_id.is_not(None),
        ServiceRequestDraft.updated_at >= start,
        ServiceRequestDraft.updated_at < end,
    ]
    return int(
        await _scalar(
            db,
            select(func.count(distinct(ServiceRequestDraft.session_id))).where(*draft_filters),
        )
    )


async def compute_users(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    agent: str | None = None,
    user_id: UUID | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    t_filters = _trace_filters(start, end, agent, user_id)
    token_stmt = (
        select(
            AgentTrace.user_id,
            func.count(distinct(AgentTrace.session_id)).label("sessions"),
            func.count(AgentTrace.id).label("turns"),
            func.coalesce(func.sum(AgentLLMCall.input_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(AgentLLMCall.output_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(AgentLLMCall.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(AgentLLMCall.estimated_cost), 0).label("total_cost"),
        )
        .select_from(AgentTrace)
        .outerjoin(AgentLLMCall, AgentLLMCall.trace_id == AgentTrace.id)
        .where(*t_filters)
        .group_by(AgentTrace.user_id)
        .order_by(func.coalesce(func.sum(AgentLLMCall.estimated_cost), 0).desc())
    )
    all_rows = (await db.execute(token_stmt)).all()
    total = len(all_rows)
    start_idx = max(0, (page - 1) * page_size)
    page_rows = all_rows[start_idx : start_idx + page_size]

    items: list[dict[str, Any]] = []
    for row in page_rows:
        uid = row.user_id
        sr_submitted = int(
            await _scalar(
                db,
                select(func.count(distinct(AnalyticsEvent.session_id))).where(
                    AnalyticsEvent.user_id == uid,
                    AnalyticsEvent.event_name == EVENT_SR_SUBMITTED,
                    AnalyticsEvent.occurred_at >= start,
                    AnalyticsEvent.occurred_at < end,
                ),
            )
        )
        avg_active = await _scalar(
            db,
            select(func.avg(ChatSession.client_active_ms)).where(
                ChatSession.user_id == uid,
                ChatSession.created_at >= start,
                ChatSession.created_at < end,
            ),
            default=0,
        )
        thumbs_up = int(
            await _scalar(
                db,
                select(func.count(AgentFeedback.id)).where(
                    AgentFeedback.user_id == uid,
                    AgentFeedback.feedback_type == FEEDBACK_TURN_THUMBS,
                    AgentFeedback.label == LABEL_THUMBS_UP,
                    AgentFeedback.created_at >= start,
                    AgentFeedback.created_at < end,
                ),
            )
        )
        thumbs_down = int(
            await _scalar(
                db,
                select(func.count(AgentFeedback.id)).where(
                    AgentFeedback.user_id == uid,
                    AgentFeedback.feedback_type == FEEDBACK_TURN_THUMBS,
                    AgentFeedback.label == LABEL_THUMBS_DOWN,
                    AgentFeedback.created_at >= start,
                    AgentFeedback.created_at < end,
                ),
            )
        )
        useful = int(
            await _scalar(
                db,
                select(func.count(AgentFeedback.id)).where(
                    AgentFeedback.user_id == uid,
                    AgentFeedback.feedback_type == FEEDBACK_FLOW_USEFUL,
                    AgentFeedback.label == LABEL_USEFUL,
                    AgentFeedback.created_at >= start,
                    AgentFeedback.created_at < end,
                ),
            )
        )
        not_useful = int(
            await _scalar(
                db,
                select(func.count(AgentFeedback.id)).where(
                    AgentFeedback.user_id == uid,
                    AgentFeedback.feedback_type == FEEDBACK_FLOW_USEFUL,
                    AgentFeedback.label == LABEL_NOT_USEFUL,
                    AgentFeedback.created_at >= start,
                    AgentFeedback.created_at < end,
                ),
            )
        )
        items.append(
            {
                "user_id": str(uid),
                "sessions": int(row.sessions or 0),
                "turns": int(row.turns or 0),
                "prompt_tokens": int(row.prompt_tokens or 0),
                "completion_tokens": int(row.completion_tokens or 0),
                "total_tokens": int(row.total_tokens or 0),
                "total_cost": float(row.total_cost or 0),
                "sr_submitted": sr_submitted,
                "avg_client_active_ms": float(avg_active or 0),
                "thumbs_up": thumbs_up,
                "thumbs_down": thumbs_down,
                "useful": useful,
                "not_useful": not_useful,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def compute_funnel(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    agent: str | None = None,
    user_id: UUID | None = None,
) -> dict[str, Any]:
    s_filters = _session_filters(start, end, user_id)
    t_filters = _trace_filters(start, end, agent, user_id)

    sessions_started = int(
        await _scalar(db, select(func.count(ChatSession.id)).where(*s_filters))
    )
    create_intent = int(
        await _scalar(
            db,
            select(func.count(distinct(AgentTrace.session_id))).where(
                *t_filters, AgentTrace.intent.in_(CREATE_SR_INTENTS)
            ),
        )
    )
    draft_started = int(
        await _scalar(
            db,
            select(func.count(distinct(ServiceRequestDraft.session_id))).where(
                ServiceRequestDraft.created_at >= start,
                ServiceRequestDraft.created_at < end,
                ServiceRequestDraft.collected_data != {},
            ),
        )
    )
    preview = int(
        await _scalar(
            db,
            select(func.count(ChatSession.id)).where(
                *s_filters,
                (
                    ChatSession.workflow_stage.in_(_PREVIEW_OR_LATER_STAGES)
                    | ChatSession.id.in_(
                        select(ServiceRequestDraft.session_id).where(
                            ServiceRequestDraft.ready_to_submit.is_(True)
                        )
                    )
                ),
            ),
        )
    )
    sr_submitted = await _count_sr_submitted(db, start, end, user_id, agent)
    lifecycle = int(
        await _scalar(
            db,
            select(func.count(distinct(AnalyticsEvent.session_id))).where(
                AnalyticsEvent.event_name == EVENT_SR_LIFECYCLE_COMPLETED,
                AnalyticsEvent.occurred_at >= start,
                AnalyticsEvent.occurred_at < end,
            ),
        )
    )
    if not lifecycle:
        lifecycle = int(
            await _scalar(
                db,
                select(func.count(ChatSession.id)).where(
                    *s_filters,
                    ChatSession.workflow_stage.in_(TERMINAL_WORKFLOW_STAGES),
                ),
            )
        )

    submitted_sessions = select(AnalyticsEvent.session_id).where(
        AnalyticsEvent.event_name == EVENT_SR_SUBMITTED
    )
    drop_rows = (
        await db.execute(
            select(ChatSession.workflow_stage, func.count(ChatSession.id))
            .where(
                *s_filters,
                ChatSession.workflow_stage.is_not(None),
                ChatSession.id.not_in(submitted_sessions),
            )
            .group_by(ChatSession.workflow_stage)
        )
    ).all()
    drop_off = [
        {"workflow_stage": stage or "UNKNOWN", "last_sessions": int(count)}
        for stage, count in drop_rows
    ]

    return {
        "steps": [
            {"key": "sessions_started", "count": sessions_started},
            {"key": "create_intent", "count": create_intent},
            {"key": "draft_started", "count": draft_started},
            {"key": "preview_or_confirm", "count": preview},
            {"key": "sr_submitted", "count": sr_submitted},
            {"key": "lifecycle_completed", "count": lifecycle},
        ],
        "drop_off_by_stage": drop_off,
    }


async def compute_feedback(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    user_id: UUID | None = None,
) -> dict[str, Any]:
    filters: list[Any] = [
        AgentFeedback.created_at >= start,
        AgentFeedback.created_at < end,
    ]
    if user_id:
        filters.append(AgentFeedback.user_id == user_id)

    thumbs_up = int(
        await _scalar(
            db,
            select(func.count(AgentFeedback.id)).where(
                *filters,
                AgentFeedback.feedback_type == FEEDBACK_TURN_THUMBS,
                AgentFeedback.label == LABEL_THUMBS_UP,
            ),
        )
    )
    thumbs_down = int(
        await _scalar(
            db,
            select(func.count(AgentFeedback.id)).where(
                *filters,
                AgentFeedback.feedback_type == FEEDBACK_TURN_THUMBS,
                AgentFeedback.label == LABEL_THUMBS_DOWN,
            ),
        )
    )
    useful = int(
        await _scalar(
            db,
            select(func.count(AgentFeedback.id)).where(
                *filters,
                AgentFeedback.feedback_type == FEEDBACK_FLOW_USEFUL,
                AgentFeedback.label == LABEL_USEFUL,
            ),
        )
    )
    not_useful = int(
        await _scalar(
            db,
            select(func.count(AgentFeedback.id)).where(
                *filters,
                AgentFeedback.feedback_type == FEEDBACK_FLOW_USEFUL,
                AgentFeedback.label == LABEL_NOT_USEFUL,
            ),
        )
    )
    csat_avg = await _scalar(
        db,
        select(func.avg(AgentFeedback.score)).where(
            *filters, AgentFeedback.feedback_type == FEEDBACK_CSAT
        ),
        default=None,
    )
    csat_n = int(
        await _scalar(
            db,
            select(func.count(AgentFeedback.id)).where(
                *filters, AgentFeedback.feedback_type == FEEDBACK_CSAT
            ),
        )
    )
    # Use ->> so Postgres groups on the extracted text, not the JSONB column
    # (GROUP BY metadata["reason"].astext compiles as GROUP BY metadata and 500s).
    reason_expr = func.coalesce(
        AgentFeedback.metadata_.op("->>")("reason"),
        "UNSPECIFIED",
    )
    reason_rows = (
        await db.execute(
            select(reason_expr, func.count(AgentFeedback.id))
            .where(
                *filters,
                AgentFeedback.feedback_type == FEEDBACK_FLOW_USEFUL,
                AgentFeedback.label == LABEL_NOT_USEFUL,
            )
            .group_by(reason_expr)
        )
    ).all()
    reasons = [
        {"reason": (reason or "UNSPECIFIED"), "count": int(count)}
        for reason, count in reason_rows
        if reason or count
    ]
    return {
        "thumbs_up": thumbs_up,
        "thumbs_down": thumbs_down,
        "thumbs_up_rate": ratio(thumbs_up, thumbs_up + thumbs_down),
        "useful": useful,
        "not_useful": not_useful,
        "usefulness_rate": ratio(useful, useful + not_useful),
        "csat_avg": float(csat_avg) if csat_avg is not None else None,
        "csat_n": csat_n,
        "not_useful_reasons": reasons,
    }
