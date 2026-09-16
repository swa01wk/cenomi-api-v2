"""Queries behind the AI Command Center (ticket 3784).

Extends :class:`AnalyticsRepository` with the KPIs the Command Center needs
and the existing dashboard does not compute.  Every definition here is the one
in ``docs/ai-command-center-full-plan.md`` §3 — the K-numbers in the docstrings
point there — so a number on the page can be traced to one query.

Conventions shared with the base repository:

* a *question* is a trace whose input is text the user typed
  (``_is_real_question``); silent refresh turns never count;
* windows are inclusive on both ends (``AnalyticsFilters``);
* SR outcomes come from ``analytics_events``, scoped to an agent through the
  sessions that agent handled.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text, and_, cast, func, literal, or_, select
from sqlalchemy.sql import ColumnElement

from app.agents.agent_display import agent_display_name
from app.db.models import (
    AgentFeedback,
    AgentToolCall,
    AgentTrace,
    AnalyticsEvent,
    ChatSession,
)
from app.observability.analytics_constants import (
    CREATE_SR_INTENTS,
    EVENT_FIELD_CORRECTED,
    EVENT_SR_APPROVED,
    EVENT_SR_LIFECYCLE_COMPLETED,
    EVENT_SR_REJECTED,
    EVENT_SR_SUBMITTED,
)
from app.observability.display_labels import tool_label
from app.observability.repositories.analytics_repo import (
    AnalyticsFilters,
    AnalyticsRepository,
    _is_real_question,
)

_NOT_ANSWERED = ("UNANSWERED", "FAILED")
_USER_RATING = "USER_RATING"
_FAILURE_PREFIX_CHARS = 80


def _event_clauses(filters: AnalyticsFilters, *names: str) -> list[ColumnElement[bool]]:
    """Window + agent/user scope for ``analytics_events`` rows."""
    clauses: list[ColumnElement[bool]] = [AnalyticsEvent.event_name.in_(names)]
    if filters.from_date is not None:
        clauses.append(AnalyticsEvent.occurred_at >= filters.from_date)
    if filters.to_date is not None:
        clauses.append(AnalyticsEvent.occurred_at <= filters.to_date)
    if filters.user_id is not None:
        clauses.append(AnalyticsEvent.user_id == filters.user_id)
    if filters.agent is not None:
        clauses.append(
            AnalyticsEvent.session_id.in_(
                select(AgentTrace.session_id).where(
                    AgentTrace.active_agent == filters.agent
                )
            )
        )
    return clauses


def _sr_key() -> ColumnElement[Any]:
    """One SR per event: its ``sr_id``, or the event id when none was captured."""
    return func.coalesce(
        AnalyticsEvent.properties.op("->>")("sr_id"), cast(AnalyticsEvent.id, Text())
    )


def _normalised_error(column: Any) -> ColumnElement[Any]:
    """Collapse whitespace and cut to a prefix so variants of one error group."""
    return func.left(
        func.regexp_replace(func.coalesce(column, ""), r"\s+", " ", "g"),
        _FAILURE_PREFIX_CHARS,
    )


class CommandCenterRepository(AnalyticsRepository):
    """Aggregate reads for the four Command Center sections."""

    # ------------------------------------------------------------------
    # Latency (K15)
    # ------------------------------------------------------------------

    async def latency_percentiles(self, filters: AnalyticsFilters) -> dict[str, int | None]:
        """P50 / P95 / P99 end-to-end turn latency in one pass."""
        ordered = AgentTrace.total_latency_ms.asc()
        row = (
            await self._session.execute(
                select(
                    func.percentile_cont(0.5).within_group(ordered).label("p50"),
                    func.percentile_cont(0.95).within_group(ordered).label("p95"),
                    func.percentile_cont(0.99).within_group(ordered).label("p99"),
                ).where(*filters.trace_clauses(), AgentTrace.total_latency_ms.is_not(None))
            )
        ).one()
        return {
            key: (round(float(getattr(row, key))) if getattr(row, key) is not None else None)
            for key in ("p50", "p95", "p99")
        }

    # ------------------------------------------------------------------
    # Users (K1, K2, K3)
    # ------------------------------------------------------------------

    async def user_activity(self, filters: AnalyticsFilters) -> dict[str, int]:
        """Active, new and repeat users, plus the questions they asked.

        *New* means the user's first trace ever — across every agent — falls in
        the window, so a long-time help-agent user who opens their first SR is
        not counted as new to the assistant.
        """
        active = (
            select(
                AgentTrace.user_id.label("user_id"),
                func.count(func.distinct(AgentTrace.session_id)).label("sessions"),
                func.count(AgentTrace.id).label("questions"),
            )
            .where(*filters.trace_clauses(), _is_real_question())
            .group_by(AgentTrace.user_id)
            .subquery()
        )
        first_seen = (
            select(
                AgentTrace.user_id.label("user_id"),
                func.min(AgentTrace.created_at).label("first_at"),
            )
            .group_by(AgentTrace.user_id)
            .subquery()
        )
        is_new: list[ColumnElement[bool]] = []
        if filters.from_date is not None:
            is_new.append(first_seen.c.first_at >= filters.from_date)
        if filters.to_date is not None:
            is_new.append(first_seen.c.first_at <= filters.to_date)

        new_count = (
            func.count(active.c.user_id).filter(and_(*is_new))
            if is_new
            else func.count(active.c.user_id)
        )
        row = (
            await self._session.execute(
                select(
                    func.count(active.c.user_id).label("active"),
                    new_count.label("new"),
                    func.count(active.c.user_id)
                    .filter(active.c.sessions >= 2)
                    .label("repeat"),
                    func.coalesce(func.sum(active.c.questions), 0).label("questions"),
                ).select_from(
                    active.outerjoin(first_seen, first_seen.c.user_id == active.c.user_id)
                )
            )
        ).one()
        return {
            "active_users": int(row.active or 0),
            "new_users": int(row.new or 0),
            "repeat_users": int(row.repeat or 0),
            "questions": int(row.questions or 0),
        }

    # ------------------------------------------------------------------
    # Conversation quality (K10, K13)
    # ------------------------------------------------------------------

    async def containment(self, filters: AnalyticsFilters) -> dict[str, int]:
        """Sessions whose every question was answered (none UNANSWERED/FAILED)."""
        per_session = (
            select(
                AgentTrace.session_id.label("session_id"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status.in_(_NOT_ANSWERED))
                .label("not_answered"),
            )
            .where(*filters.trace_clauses(), _is_real_question())
            .group_by(AgentTrace.session_id)
            .subquery()
        )
        row = (
            await self._session.execute(
                select(
                    func.count(per_session.c.session_id).label("sessions"),
                    func.count(per_session.c.session_id)
                    .filter(per_session.c.not_answered == 0)
                    .label("contained"),
                )
            )
        ).one()
        return {"sessions": int(row.sessions or 0), "contained": int(row.contained or 0)}

    async def feedback_participation(self, filters: AnalyticsFilters) -> dict[str, int]:
        """Sessions with at least one star rating, over sessions with a question."""
        asked = (
            select(AgentTrace.session_id.label("session_id"))
            .where(*filters.trace_clauses(), _is_real_question())
            .distinct()
            .subquery()
        )
        rated = select(AgentFeedback.session_id).where(
            AgentFeedback.feedback_type == _USER_RATING,
            AgentFeedback.session_id.is_not(None),
        )
        row = (
            await self._session.execute(
                select(
                    func.count(asked.c.session_id).label("sessions"),
                    func.count(asked.c.session_id)
                    .filter(asked.c.session_id.in_(rated))
                    .label("rated"),
                )
            )
        ).one()
        return {"sessions": int(row.sessions or 0), "rated_sessions": int(row.rated or 0)}

    async def answer_trend(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        """Per-day Answer Rate over questions, with its classification coverage.

        ``daily_series`` divides by all turns; this divides by classified
        questions so the line matches the Answer Rate card.
        """
        day = func.date_trunc("day", AgentTrace.created_at).label("day")
        rows = await self._session.execute(
            select(
                day,
                func.count(AgentTrace.id).label("questions"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status.is_not(None))
                .label("classified"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status == "ANSWERED")
                .label("answered"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status.in_(_NOT_ANSWERED))
                .label("not_answered"),
            )
            .where(*filters.trace_clauses(), _is_real_question())
            .group_by(day)
            .order_by(day)
        )
        return [
            {
                "date": r.day.date().isoformat(),
                "questions": r.questions or 0,
                "classified": r.classified or 0,
                "answered": r.answered or 0,
                "not_answered": r.not_answered or 0,
            }
            for r in rows
        ]

    async def channel_distribution(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        """Questions, sessions and users per chat surface ("Usage by Channel").

        Traces written before migration 010 carry no channel and are grouped
        as ``unknown`` together with clients that did not send one.
        """
        channel = func.coalesce(AgentTrace.channel, "unknown").label("channel")
        rows = await self._session.execute(
            select(
                channel,
                func.count(AgentTrace.id).label("questions"),
                func.count(func.distinct(AgentTrace.session_id)).label("sessions"),
                func.count(func.distinct(AgentTrace.user_id)).label("users"),
            )
            .where(*filters.trace_clauses(), _is_real_question())
            .group_by(channel)
            .order_by(func.count(AgentTrace.id).desc())
        )
        return [
            {
                "channel": r.channel,
                "questions": r.questions or 0,
                "sessions": r.sessions or 0,
                "users": r.users or 0,
            }
            for r in rows
        ]

    async def intent_distribution(
        self, filters: AnalyticsFilters, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Most common resolved intents among questions (K31)."""
        intent = func.coalesce(AgentTrace.intent, "UNRESOLVED").label("intent")
        rows = await self._session.execute(
            select(intent, func.count(AgentTrace.id).label("count"))
            .where(*filters.trace_clauses(), _is_real_question())
            .group_by(intent)
            .order_by(func.count(AgentTrace.id).desc())
            .limit(limit)
        )
        return [{"intent": r.intent, "count": r.count or 0} for r in rows]

    # ------------------------------------------------------------------
    # Reliability (K16, K30)
    # ------------------------------------------------------------------

    async def failure_reasons(
        self, filters: AnalyticsFilters, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Top failure messages from failed turns and failed platform calls."""
        turn_reason = _normalised_error(AgentTrace.error_message).label("reason")
        turn_rows = await self._session.execute(
            select(
                turn_reason,
                func.count(AgentTrace.id).label("count"),
                func.max(AgentTrace.created_at).label("last_seen"),
            )
            .where(*filters.trace_clauses(), AgentTrace.status == "FAILED")
            .group_by(turn_reason)
        )

        tool_clauses: list[Any] = [AgentToolCall.success.is_(False)]
        if filters.from_date is not None:
            tool_clauses.append(AgentToolCall.created_at >= filters.from_date)
        if filters.to_date is not None:
            tool_clauses.append(AgentToolCall.created_at <= filters.to_date)
        scoped = filters.scoped_trace_ids()
        if scoped is not None:
            tool_clauses.append(AgentToolCall.trace_id.in_(scoped))
        tool_reason = _normalised_error(AgentToolCall.error_message).label("reason")
        tool_rows = await self._session.execute(
            select(
                AgentToolCall.tool_name.label("tool"),
                tool_reason,
                func.count(AgentToolCall.id).label("count"),
                func.max(AgentToolCall.created_at).label("last_seen"),
            )
            .where(*tool_clauses)
            .group_by(AgentToolCall.tool_name, tool_reason)
        )

        reasons = [
            {
                "source": "turn",
                "source_label": None,
                "reason": r.reason or "No error message recorded",
                "count": r.count or 0,
                "last_seen": r.last_seen,
            }
            for r in turn_rows
        ] + [
            {
                "source": r.tool,
                "source_label": tool_label(r.tool),
                "reason": r.reason or "No error message recorded",
                "count": r.count or 0,
                "last_seen": r.last_seen,
            }
            for r in tool_rows
        ]
        reasons.sort(key=lambda item: item["count"], reverse=True)
        return reasons[:limit]

    async def agent_scorecard(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        """Per-agent answer rate, rating, p95 latency, tool success and cost."""
        clauses = filters.trace_clauses()
        quality_rows = await self._session.execute(
            select(
                AgentTrace.active_agent.label("agent"),
                func.count(AgentTrace.id).filter(_is_real_question()).label("questions"),
                func.count(AgentTrace.id)
                .filter(_is_real_question(), AgentTrace.answer_status.is_not(None))
                .label("classified"),
                func.count(AgentTrace.id)
                .filter(_is_real_question(), AgentTrace.answer_status == "ANSWERED")
                .label("answered"),
                func.count(AgentTrace.id)
                .filter(
                    _is_real_question(), AgentTrace.answer_status.in_(_NOT_ANSWERED)
                )
                .label("not_answered"),
                func.percentile_cont(0.95)
                .within_group(AgentTrace.total_latency_ms.asc())
                .label("p95_ms"),
                func.coalesce(func.sum(AgentTrace.estimated_cost), 0).label("cost"),
            )
            .where(*clauses)
            .group_by(AgentTrace.active_agent)
        )
        rating_rows = await self._session.execute(
            select(
                AgentTrace.active_agent.label("agent"),
                func.avg(AgentFeedback.score).label("avg_rating"),
                func.count(AgentFeedback.id).label("ratings"),
            )
            .join(AgentTrace, AgentTrace.id == AgentFeedback.trace_id)
            .where(*clauses, AgentFeedback.feedback_type == _USER_RATING)
            .group_by(AgentTrace.active_agent)
        )
        tool_rows = await self._session.execute(
            select(
                AgentTrace.active_agent.label("agent"),
                func.count(AgentToolCall.id).label("calls"),
                func.count(AgentToolCall.id)
                .filter(AgentToolCall.success.is_(False))
                .label("failed"),
            )
            .join(AgentTrace, AgentTrace.id == AgentToolCall.trace_id)
            .where(*clauses)
            .group_by(AgentTrace.active_agent)
        )
        ratings = {r.agent: r for r in rating_rows}
        tools = {r.agent: r for r in tool_rows}

        scorecard: list[dict[str, Any]] = []
        for r in quality_rows:
            rating = ratings.get(r.agent)
            tool = tools.get(r.agent)
            scorecard.append(
                {
                    "agent": r.agent or "unrouted",
                    "agent_label": agent_display_name(r.agent),
                    "questions": r.questions or 0,
                    "classified": r.classified or 0,
                    "answered": r.answered or 0,
                    "not_answered": r.not_answered or 0,
                    "p95_latency_ms": round(float(r.p95_ms)) if r.p95_ms is not None else None,
                    "cost": float(r.cost or 0),
                    "avg_rating": (
                        round(float(rating.avg_rating), 2)
                        if rating is not None and rating.avg_rating is not None
                        else None
                    ),
                    "ratings": rating.ratings if rating is not None else 0,
                    "tool_calls": tool.calls if tool is not None else 0,
                    "tool_failures": tool.failed if tool is not None else 0,
                }
            )
        scorecard.sort(key=lambda item: item["questions"], reverse=True)
        return scorecard

    # ------------------------------------------------------------------
    # Service requests (K19–K29)
    # ------------------------------------------------------------------

    async def sr_outcomes(self, filters: AnalyticsFilters) -> dict[str, Any]:
        """SRs created, submission / completion inputs, approvals and rejections."""
        events = (
            await self._session.execute(
                select(
                    AnalyticsEvent.event_name.label("name"),
                    func.count(func.distinct(AnalyticsEvent.session_id)).label("sessions"),
                    func.count(func.distinct(_sr_key())).label("srs"),
                    func.count(AnalyticsEvent.id).label("events"),
                )
                .where(
                    *_event_clauses(
                        filters,
                        EVENT_SR_SUBMITTED,
                        EVENT_SR_LIFECYCLE_COMPLETED,
                        EVENT_SR_APPROVED,
                        EVENT_SR_REJECTED,
                    )
                )
                .group_by(AnalyticsEvent.event_name)
            )
        ).all()
        by_name = {r.name: r for r in events}

        flow = func.coalesce(AnalyticsEvent.properties.op("->>")("flow"), "unknown")
        decisions = await self._session.execute(
            select(
                flow.label("flow"),
                func.count(AnalyticsEvent.id)
                .filter(AnalyticsEvent.event_name == EVENT_SR_APPROVED)
                .label("approved"),
                func.count(AnalyticsEvent.id)
                .filter(AnalyticsEvent.event_name == EVENT_SR_REJECTED)
                .label("rejected"),
            )
            .where(*_event_clauses(filters, EVENT_SR_APPROVED, EVENT_SR_REJECTED))
            .group_by(flow)
            .order_by(flow)
        )

        submitted_sessions = select(AnalyticsEvent.session_id).where(
            *_event_clauses(filters, EVENT_SR_SUBMITTED)
        )
        intent_sessions = (
            select(AgentTrace.session_id.label("session_id"))
            .where(*filters.trace_clauses(), AgentTrace.intent.in_(CREATE_SR_INTENTS))
            .distinct()
            .subquery()
        )
        funnel = (
            await self._session.execute(
                select(
                    func.count(intent_sessions.c.session_id).label("create_intent"),
                    func.count(intent_sessions.c.session_id)
                    .filter(intent_sessions.c.session_id.in_(submitted_sessions))
                    .label("create_intent_submitted"),
                )
            )
        ).one()
        # Completion is matched on sr_id, not session: a handover is created in
        # the tenant's session and completed in the RDD reviewer's.
        completed_srs = select(AnalyticsEvent.properties.op("->>")("sr_id")).where(
            AnalyticsEvent.event_name == EVENT_SR_LIFECYCLE_COMPLETED,
            AnalyticsEvent.properties.op("->>")("sr_id").is_not(None),
        )
        submitted_row = (
            await self._session.execute(
                select(
                    func.count(func.distinct(AnalyticsEvent.session_id)).label("sessions"),
                    func.count(func.distinct(_sr_key())).label("srs"),
                    func.count(func.distinct(_sr_key()))
                    .filter(AnalyticsEvent.properties.op("->>")("sr_id").in_(completed_srs))
                    .label("completed"),
                ).where(*_event_clauses(filters, EVENT_SR_SUBMITTED))
            )
        ).one()

        def _count(name: str, field: str) -> int:
            row = by_name.get(name)
            return int(getattr(row, field) or 0) if row is not None else 0

        return {
            "srs_created": int(submitted_row.srs or 0),
            "srs_completed": int(submitted_row.completed or 0),
            "submitted_sessions": int(submitted_row.sessions or 0),
            "create_intent_sessions": int(funnel.create_intent or 0),
            "create_intent_submitted": int(funnel.create_intent_submitted or 0),
            "approvals": _count(EVENT_SR_APPROVED, "events"),
            "rejections": _count(EVENT_SR_REJECTED, "events"),
            "decisions_by_flow": [
                {
                    "flow": r.flow,
                    "approved": r.approved or 0,
                    "rejected": r.rejected or 0,
                }
                for r in decisions
            ],
        }

    async def automation_mix(self, filters: AnalyticsFilters) -> dict[str, int]:
        """Submitted sessions split into automated vs assisted, plus failed flows (K23).

        *Assisted*: a submitted session where the agent could not answer a
        question or the user corrected a field.  *Failed*: a session that
        showed create intent in the window and never submitted.
        """
        submitted = (
            select(AnalyticsEvent.session_id.label("session_id"))
            .where(*_event_clauses(filters, EVENT_SR_SUBMITTED))
            .distinct()
            .subquery()
        )
        not_answered_sessions = select(AgentTrace.session_id).where(
            _is_real_question(), AgentTrace.answer_status.in_(_NOT_ANSWERED)
        )
        corrected_sessions = select(AnalyticsEvent.session_id).where(
            AnalyticsEvent.event_name == EVENT_FIELD_CORRECTED
        )
        mix = (
            await self._session.execute(
                select(
                    func.count(submitted.c.session_id).label("submitted"),
                    func.count(submitted.c.session_id)
                    .filter(
                        or_(
                            submitted.c.session_id.in_(not_answered_sessions),
                            submitted.c.session_id.in_(corrected_sessions),
                        )
                    )
                    .label("assisted"),
                    func.count(submitted.c.session_id)
                    .filter(submitted.c.session_id.in_(corrected_sessions))
                    .label("corrected"),
                )
            )
        ).one()

        ever_submitted = select(AnalyticsEvent.session_id).where(
            AnalyticsEvent.event_name == EVENT_SR_SUBMITTED
        )
        failed = (
            await self._session.execute(
                select(func.count(func.distinct(AgentTrace.session_id))).where(
                    *filters.trace_clauses(),
                    AgentTrace.intent.in_(CREATE_SR_INTENTS),
                    AgentTrace.session_id.not_in(ever_submitted),
                )
            )
        ).scalar_one()

        submitted_count = int(mix.submitted or 0)
        assisted = int(mix.assisted or 0)
        return {
            "submitted_sessions": submitted_count,
            "automated": submitted_count - assisted,
            "assisted": assisted,
            "corrected": int(mix.corrected or 0),
            "failed": int(failed or 0),
        }

    async def tasks_daily(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        """Per-day SRs created, approvals and rejections (K28)."""
        day = func.date_trunc("day", AnalyticsEvent.occurred_at).label("day")
        rows = await self._session.execute(
            select(
                day,
                func.count(func.distinct(_sr_key()))
                .filter(AnalyticsEvent.event_name == EVENT_SR_SUBMITTED)
                .label("created"),
                func.count(AnalyticsEvent.id)
                .filter(AnalyticsEvent.event_name == EVENT_SR_APPROVED)
                .label("approved"),
                func.count(AnalyticsEvent.id)
                .filter(AnalyticsEvent.event_name == EVENT_SR_REJECTED)
                .label("rejected"),
            )
            .where(
                *_event_clauses(
                    filters, EVENT_SR_SUBMITTED, EVENT_SR_APPROVED, EVENT_SR_REJECTED
                )
            )
            .group_by(day)
            .order_by(day)
        )
        return [
            {
                "date": r.day.date().isoformat(),
                "created": r.created or 0,
                "approved": r.approved or 0,
                "rejected": r.rejected or 0,
                "total": (r.created or 0) + (r.approved or 0) + (r.rejected or 0),
            }
            for r in rows
        ]

    async def submitted_cost(self, filters: AnalyticsFilters) -> float:
        """LLM spend on turns inside sessions that created an SR (K22 numerator)."""
        submitted_sessions = select(AnalyticsEvent.session_id).where(
            *_event_clauses(filters, EVENT_SR_SUBMITTED)
        )
        value = (
            await self._session.execute(
                select(func.coalesce(func.sum(AgentTrace.estimated_cost), 0)).where(
                    *filters.trace_clauses(),
                    AgentTrace.session_id.in_(submitted_sessions),
                )
            )
        ).scalar_one()
        return float(value or 0)

    async def productivity_by_agent(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        """SRs created and their LLM cost, per agent that created them (K29).

        The agent is the one on the create turn's trace; backfilled events
        have no trace, so they fall back to the session's last agent.
        """
        agent = func.coalesce(
            AgentTrace.active_agent, ChatSession.active_agent, literal("unknown")
        ).label("agent")
        completed_srs = select(AnalyticsEvent.properties.op("->>")("sr_id")).where(
            AnalyticsEvent.event_name == EVENT_SR_LIFECYCLE_COMPLETED,
            AnalyticsEvent.properties.op("->>")("sr_id").is_not(None),
        )
        created_rows = await self._session.execute(
            select(
                agent,
                func.count(func.distinct(_sr_key())).label("srs"),
                func.count(func.distinct(_sr_key()))
                .filter(AnalyticsEvent.properties.op("->>")("sr_id").in_(completed_srs))
                .label("completed"),
                func.count(func.distinct(AnalyticsEvent.session_id)).label("sessions"),
            )
            .select_from(AnalyticsEvent)
            .outerjoin(AgentTrace, AgentTrace.id == AnalyticsEvent.trace_id)
            .outerjoin(ChatSession, ChatSession.id == AnalyticsEvent.session_id)
            .where(*_event_clauses(filters, EVENT_SR_SUBMITTED))
            .group_by(agent)
        )
        submitted_sessions = select(AnalyticsEvent.session_id).where(
            *_event_clauses(filters, EVENT_SR_SUBMITTED)
        )
        cost_rows = await self._session.execute(
            select(
                AgentTrace.active_agent.label("agent"),
                func.coalesce(func.sum(AgentTrace.estimated_cost), 0).label("cost"),
            )
            .where(
                *filters.trace_clauses(),
                AgentTrace.session_id.in_(submitted_sessions),
            )
            .group_by(AgentTrace.active_agent)
        )
        costs = {r.agent: r for r in cost_rows}

        items: list[dict[str, Any]] = []
        for r in created_rows:
            cost_row = costs.get(r.agent)
            cost = float(cost_row.cost or 0) if cost_row is not None else 0.0
            items.append(
                {
                    "agent": r.agent,
                    "agent_label": agent_display_name(r.agent),
                    "srs_created": r.srs or 0,
                    "sessions": r.sessions or 0,
                    "srs_completed": r.completed or 0,
                    "cost": cost,
                }
            )
        items.sort(key=lambda item: item["srs_created"], reverse=True)
        return items

    async def decision_times(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        """Hours from SR creation to its first reviewer decision, per flow (K27).

        Joined on ``sr_id``: the create and the decision usually happen in
        different sessions by different people.  Decisions without a known
        creation (SR created outside the assistant) are skipped.
        """
        # One expression object per GROUP BY: rebuilding ``->> 'sr_id'`` binds a
        # fresh parameter, and Postgres will not match it to the select list.
        created_sr = AnalyticsEvent.properties.op("->>")("sr_id")
        created = (
            select(
                created_sr.label("sr_id"),
                func.min(AnalyticsEvent.occurred_at).label("created_at"),
            )
            .where(AnalyticsEvent.event_name == EVENT_SR_SUBMITTED, created_sr.is_not(None))
            .group_by(created_sr)
            .subquery()
        )
        decision_sr = AnalyticsEvent.properties.op("->>")("sr_id")
        decision_flow = func.coalesce(AnalyticsEvent.properties.op("->>")("flow"), "unknown")
        decided = (
            select(
                decision_sr.label("sr_id"),
                decision_flow.label("flow"),
                func.min(AnalyticsEvent.occurred_at).label("decided_at"),
            )
            .where(
                *_event_clauses(filters, EVENT_SR_APPROVED, EVENT_SR_REJECTED),
                decision_sr.is_not(None),
            )
            .group_by(decision_sr, decision_flow)
            .subquery()
        )
        hours = (
            func.extract("epoch", decided.c.decided_at - created.c.created_at) / 3600.0
        )
        rows = await self._session.execute(
            select(
                decided.c.flow,
                func.count(decided.c.sr_id).label("srs"),
                func.avg(hours).label("avg_hours"),
                func.percentile_cont(0.5).within_group(hours.asc()).label("median_hours"),
            )
            .select_from(decided.join(created, created.c.sr_id == decided.c.sr_id))
            .where(decided.c.decided_at >= created.c.created_at)
            .group_by(decided.c.flow)
            .order_by(decided.c.flow)
        )
        return [
            {
                "flow": r.flow,
                "srs": r.srs or 0,
                "avg_hours": round(float(r.avg_hours), 2) if r.avg_hours is not None else None,
                "median_hours": (
                    round(float(r.median_hours), 2) if r.median_hours is not None else None
                ),
            }
            for r in rows
        ]

    async def sr_log(
        self, filters: AnalyticsFilters, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """SRs the assistant created, newest first, with how far each got."""
        clauses = _event_clauses(filters, EVENT_SR_SUBMITTED)
        total = (
            await self._session.execute(select(func.count(AnalyticsEvent.id)).where(*clauses))
        ).scalar_one()

        sr_id = AnalyticsEvent.properties.op("->>")("sr_id")
        # Matched on sr_id: completion is recorded in the reviewer's session,
        # not in the session that created the SR.
        completion = AnalyticsEvent.__table__.alias("completion")
        lifecycle = (
            select(func.count(completion.c.id))
            .where(
                completion.c.event_name == EVENT_SR_LIFECYCLE_COMPLETED,
                completion.c.properties.op("->>")("sr_id") == sr_id,
            )
            .scalar_subquery()
        )
        outer_event = AnalyticsEvent.__table__.alias("decision")
        last_decision = (
            select(outer_event.c.event_name)
            .where(
                outer_event.c.event_name.in_([EVENT_SR_APPROVED, EVENT_SR_REJECTED]),
                outer_event.c.properties.op("->>")("sr_id") == sr_id,
            )
            .order_by(outer_event.c.occurred_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        agent = func.coalesce(AgentTrace.active_agent, ChatSession.active_agent)
        rows = await self._session.execute(
            select(
                AnalyticsEvent.session_id,
                AnalyticsEvent.user_id,
                AnalyticsEvent.occurred_at,
                sr_id.label("sr_id"),
                agent.label("agent"),
                ChatSession.workflow_stage.label("workflow_stage"),
                lifecycle.label("lifecycle_events"),
                last_decision.label("last_decision"),
            )
            .select_from(AnalyticsEvent)
            .outerjoin(AgentTrace, AgentTrace.id == AnalyticsEvent.trace_id)
            .outerjoin(ChatSession, ChatSession.id == AnalyticsEvent.session_id)
            .where(*clauses)
            .order_by(AnalyticsEvent.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = [
            {
                "session_id": str(r.session_id),
                "user_id": str(r.user_id),
                "created_at": r.occurred_at,
                "sr_id": r.sr_id,
                "agent": r.agent,
                "agent_label": agent_display_name(r.agent) if r.agent else None,
                "workflow_stage": r.workflow_stage,
                "lifecycle_completed": bool(r.lifecycle_events),
                "last_decision": r.last_decision,
            }
            for r in rows
        ]
        return items, int(total or 0)
