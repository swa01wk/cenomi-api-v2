"""Analytics repository — aggregate reads over the agent observability tables.

All queries are read-only and computed live from ``agent_traces``,
``agent_feedback``, ``agent_llm_calls``, ``chat_messages`` and
``service_request_chat_audit_logs``.  No rollup table exists yet; when volume
makes these slow, a nightly ``agent_metrics_daily`` job can back the same
method signatures without changing the API layer.

Grouping convention: every "per day" bucket is a UTC date, so a caller
comparing two windows always compares the same boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import Integer, Text, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.agent_display import agent_display_name

from app.db.models import (
    AgentFeedback,
    AgentLLMCall,
    AgentTrace,
    ChatMessage,
    ServiceRequestChatAuditLog,
)

log = structlog.get_logger(__name__)

# Turns more than this far apart belong to separate visits, so a session left
# open overnight does not report as a multi-hour "time spent".
SESSION_IDLE_CUTOFF_MINUTES = 30

_USER_RATING = "USER_RATING"

# Inputs that are not questions a user asked.  The UI sends a blank or
# whitespace message for silent refresh turns (a document upload, a form-bridge
# patch, opening an existing SR), and "none" for action-only turns.  Counting
# them would inflate question volume and dominate the top-questions report —
# on live data they are roughly a third of all traces.
_NON_QUESTION_INPUTS: frozenset[str] = frozenset({"", "none"})


def _is_real_question() -> Any:
    """SQL clause matching traces whose input is a question a user typed."""
    return func.lower(func.trim(func.coalesce(AgentTrace.input_message, ""))).notin_(
        _NON_QUESTION_INPUTS
    )


@dataclass(frozen=True, slots=True)
class AnalyticsFilters:
    """Common filter set accepted by every analytics endpoint."""

    from_date: datetime | None = None
    to_date: datetime | None = None
    agent: str | None = None
    user_id: UUID | None = None

    def trace_clauses(self) -> list[Any]:
        clauses: list[Any] = []
        if self.from_date is not None:
            clauses.append(AgentTrace.created_at >= self.from_date)
        if self.to_date is not None:
            clauses.append(AgentTrace.created_at <= self.to_date)
        if self.agent is not None:
            clauses.append(AgentTrace.active_agent == self.agent)
        if self.user_id is not None:
            clauses.append(AgentTrace.user_id == self.user_id)
        return clauses


class AnalyticsRepository:
    """Aggregate reads powering the agent analytics dashboard."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    async def turn_totals(self, filters: AnalyticsFilters) -> dict[str, Any]:
        """Volume, outcome split, latency and cost for the window."""
        result = await self._session.execute(
            select(
                func.count(AgentTrace.id).label("total_turns"),
                # Turns the user actually asked something, as opposed to the
                # silent refresh turns the UI fires in the background.
                func.count(AgentTrace.id)
                .filter(_is_real_question())
                .label("questions"),
                # Outcome counts are scoped to real questions on both sides of
                # the ratio. Silent refresh turns are classified too (they
                # produce a reply), so counting them would inflate the
                # numerator and denominator and make "accuracy" a statement
                # about background syncs rather than about user questions.
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status == "ANSWERED", _is_real_question())
                .label("answered"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status == "PARTIAL", _is_real_question())
                .label("partial"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status == "UNANSWERED", _is_real_question())
                .label("unanswered"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status == "FAILED", _is_real_question())
                .label("failed_answers"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status.is_not(None), _is_real_question())
                .label("classified"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.status == "FAILED")
                .label("failed_traces"),
                func.count(func.distinct(AgentTrace.session_id)).label("sessions"),
                func.count(func.distinct(AgentTrace.user_id)).label("users"),
                func.avg(AgentTrace.total_latency_ms).label("avg_latency_ms"),
                func.coalesce(func.sum(AgentTrace.total_token_count), 0).label("tokens"),
                func.coalesce(func.sum(AgentTrace.estimated_cost), 0).label("cost"),
            ).where(*filters.trace_clauses())
        )
        row = result.one()

        total = row.total_turns or 0
        classified = row.classified or 0
        answered = row.answered or 0
        unanswered = row.unanswered or 0
        failed_answers = row.failed_answers or 0

        # Success = the user got an answer. Failure is its complement over the
        # same denominator, so the two always sum to 1 for classified turns.
        success_rate = (answered / classified) if classified else 0.0
        failure_rate = (
            (unanswered + failed_answers) / classified if classified else 0.0
        )

        return {
            "total_turns": total,
            "questions": row.questions or 0,
            "answered": answered,
            "partial": row.partial or 0,
            "unanswered": unanswered,
            "failed": failed_answers,
            "classified": classified,
            "failed_traces": row.failed_traces or 0,
            "success_rate": round(success_rate, 4),
            "failure_rate": round(failure_rate, 4),
            "sessions": row.sessions or 0,
            "unique_users": row.users or 0,
            "avg_latency_ms": (
                round(float(row.avg_latency_ms)) if row.avg_latency_ms is not None else 0
            ),
            "total_tokens": int(row.tokens or 0),
            "total_cost": float(row.cost or 0),
        }

    async def latency_percentile(
        self, filters: AnalyticsFilters, percentile: float = 0.95
    ) -> int | None:
        """Return the p-th percentile of turn latency, or None when empty."""
        result = await self._session.execute(
            select(
                func.percentile_cont(percentile).within_group(
                    AgentTrace.total_latency_ms.asc()
                )
            ).where(*filters.trace_clauses(), AgentTrace.total_latency_ms.is_not(None))
        )
        value = result.scalar_one_or_none()
        return round(float(value)) if value is not None else None

    async def rating_totals(self, filters: AnalyticsFilters) -> dict[str, Any]:
        """Star-rating volume, average and 1–5 distribution for the window."""
        clauses = [AgentFeedback.feedback_type == _USER_RATING]
        if filters.from_date is not None:
            clauses.append(AgentFeedback.created_at >= filters.from_date)
        if filters.to_date is not None:
            clauses.append(AgentFeedback.created_at <= filters.to_date)
        if filters.user_id is not None:
            clauses.append(AgentFeedback.user_id == filters.user_id)
        if filters.agent is not None:
            # Ratings carry no agent of their own — scope through the trace.
            clauses.append(
                AgentFeedback.trace_id.in_(
                    select(AgentTrace.id).where(AgentTrace.active_agent == filters.agent)
                )
            )

        result = await self._session.execute(
            select(
                func.count(AgentFeedback.id).label("count"),
                func.avg(AgentFeedback.score).label("avg_score"),
                func.count(AgentFeedback.id)
                .filter(AgentFeedback.comment.is_not(None))
                .label("with_comment"),
                *[
                    func.count(AgentFeedback.id)
                    .filter(AgentFeedback.score == star)
                    .label(f"star_{star}")
                    for star in range(1, 6)
                ],
                func.count(AgentFeedback.id)
                .filter(AgentFeedback.source == "help_answer")
                .label("help_count"),
                func.count(AgentFeedback.id)
                .filter(AgentFeedback.source == "sr_created")
                .label("sr_count"),
                func.avg(AgentFeedback.score)
                .filter(AgentFeedback.source == "help_answer")
                .label("help_avg"),
                func.avg(AgentFeedback.score)
                .filter(AgentFeedback.source == "sr_created")
                .label("sr_avg"),
            ).where(*clauses)
        )
        row = result.one()

        def _avg(value: Any) -> float | None:
            return round(float(value), 2) if value is not None else None

        return {
            "ratings_count": row.count or 0,
            "avg_rating": _avg(row.avg_score),
            "comments_count": row.with_comment or 0,
            "distribution": {
                str(star): getattr(row, f"star_{star}") or 0 for star in range(1, 6)
            },
            "by_source": {
                "help_answer": {
                    "count": row.help_count or 0,
                    "avg_rating": _avg(row.help_avg),
                },
                "sr_created": {
                    "count": row.sr_count or 0,
                    "avg_rating": _avg(row.sr_avg),
                },
            },
        }

    async def sr_funnel(self, filters: AnalyticsFilters) -> dict[str, Any]:
        """SR flows started vs service requests actually created."""
        # A flow is "started" when a turn is handled by a workflow agent.
        started_result = await self._session.execute(
            select(func.count(func.distinct(AgentTrace.session_id))).where(
                *filters.trace_clauses(),
                AgentTrace.active_agent.is_not(None),
                AgentTrace.active_agent.notin_(["help_agent", "notification_agent"]),
            )
        )
        started = started_result.scalar_one() or 0

        created_clauses: list[Any] = [
            ServiceRequestChatAuditLog.action == "service_request.created"
        ]
        if filters.from_date is not None:
            created_clauses.append(
                ServiceRequestChatAuditLog.created_at >= filters.from_date
            )
        if filters.to_date is not None:
            created_clauses.append(
                ServiceRequestChatAuditLog.created_at <= filters.to_date
            )
        if filters.user_id is not None:
            created_clauses.append(
                ServiceRequestChatAuditLog.actor_user_id == filters.user_id
            )
        created_result = await self._session.execute(
            select(func.count(ServiceRequestChatAuditLog.id)).where(*created_clauses)
        )
        created = created_result.scalar_one() or 0

        return {
            "flows_started": started,
            "srs_created": created,
            "completion_rate": round(created / started, 4) if started else 0.0,
        }

    async def daily_series(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        """Per-day turn volume, answered share, latency and ratings."""
        day = func.date_trunc("day", AgentTrace.created_at).label("day")
        trace_rows = await self._session.execute(
            select(
                day,
                func.count(AgentTrace.id).label("turns"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status == "ANSWERED")
                .label("answered"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status.in_(["UNANSWERED", "FAILED"]))
                .label("not_answered"),
                func.avg(AgentTrace.total_latency_ms).label("avg_latency_ms"),
                func.count(func.distinct(AgentTrace.session_id)).label("sessions"),
            )
            .where(*filters.trace_clauses())
            .group_by(day)
            .order_by(day)
        )

        rating_day = func.date_trunc("day", AgentFeedback.created_at).label("day")
        rating_clauses: list[Any] = [AgentFeedback.feedback_type == _USER_RATING]
        if filters.from_date is not None:
            rating_clauses.append(AgentFeedback.created_at >= filters.from_date)
        if filters.to_date is not None:
            rating_clauses.append(AgentFeedback.created_at <= filters.to_date)
        rating_rows = await self._session.execute(
            select(
                rating_day,
                func.count(AgentFeedback.id).label("ratings"),
                func.avg(AgentFeedback.score).label("avg_rating"),
            )
            .where(*rating_clauses)
            .group_by(rating_day)
            .order_by(rating_day)
        )
        ratings_by_day = {
            r.day.date().isoformat(): (
                r.ratings or 0,
                round(float(r.avg_rating), 2) if r.avg_rating is not None else None,
            )
            for r in rating_rows
        }

        series: list[dict[str, Any]] = []
        for row in trace_rows:
            key = row.day.date().isoformat()
            ratings, avg_rating = ratings_by_day.get(key, (0, None))
            turns = row.turns or 0
            answered = row.answered or 0
            series.append(
                {
                    "date": key,
                    "turns": turns,
                    "answered": answered,
                    "not_answered": row.not_answered or 0,
                    "answered_rate": round(answered / turns, 4) if turns else 0.0,
                    "sessions": row.sessions or 0,
                    "avg_latency_ms": (
                        round(float(row.avg_latency_ms))
                        if row.avg_latency_ms is not None
                        else 0
                    ),
                    "ratings": ratings,
                    "avg_rating": avg_rating,
                }
            )
        return series

    # ------------------------------------------------------------------
    # Questions
    # ------------------------------------------------------------------

    async def questions(
        self,
        filters: AnalyticsFilters,
        *,
        answer_status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Question log with each turn's outcome and rating, newest first."""
        clauses = filters.trace_clauses()
        if answer_status is not None:
            clauses.append(AgentTrace.answer_status == answer_status)
        if search:
            clauses.append(AgentTrace.input_message.ilike(f"%{search}%"))
        clauses.append(_is_real_question())

        total_result = await self._session.execute(
            select(func.count(AgentTrace.id)).where(*clauses)
        )
        total = total_result.scalar_one() or 0

        rating = (
            select(
                AgentFeedback.trace_id.label("trace_id"),
                func.max(AgentFeedback.score).label("score"),
                func.max(AgentFeedback.comment).label("comment"),
            )
            .where(AgentFeedback.feedback_type == _USER_RATING)
            .group_by(AgentFeedback.trace_id)
            .subquery()
        )

        rows = await self._session.execute(
            select(
                AgentTrace.id,
                AgentTrace.session_id,
                AgentTrace.user_id,
                AgentTrace.created_at,
                AgentTrace.active_agent,
                AgentTrace.intent,
                AgentTrace.input_message,
                AgentTrace.output_message,
                AgentTrace.status,
                AgentTrace.answer_status,
                AgentTrace.total_latency_ms,
                rating.c.score,
                rating.c.comment,
            )
            .outerjoin(rating, rating.c.trace_id == AgentTrace.id)
            .where(*clauses)
            .order_by(AgentTrace.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        items = [
            {
                "trace_id": str(r.id),
                "session_id": str(r.session_id),
                "user_id": str(r.user_id),
                "asked_at": r.created_at,
                "agent": r.active_agent,
                "agent_label": agent_display_name(r.active_agent, r.intent),
                "intent": r.intent,
                "question": r.input_message,
                "answer": r.output_message,
                "status": r.status,
                "answer_status": r.answer_status,
                "latency_ms": r.total_latency_ms,
                "rating": r.score,
                "comment": r.comment,
            }
            for r in rows
        ]
        return items, total

    async def top_questions(
        self, filters: AnalyticsFilters, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Most frequently asked questions, matched case-insensitively."""
        normalized = func.lower(func.trim(AgentTrace.input_message)).label("question")
        rows = await self._session.execute(
            select(
                normalized,
                func.count(AgentTrace.id).label("count"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status.in_(["UNANSWERED", "FAILED"]))
                .label("not_answered"),
            )
            .where(*filters.trace_clauses(), _is_real_question())
            .group_by(normalized)
            .order_by(func.count(AgentTrace.id).desc())
            .limit(limit)
        )
        return [
            {
                "question": r.question,
                "count": r.count or 0,
                "not_answered": r.not_answered or 0,
            }
            for r in rows
        ]

    async def session_transcript(self, session_id: UUID) -> list[dict[str, Any]]:
        """Return one session's turns: what was asked, what came back, how it rated.

        Reads from ``agent_traces`` rather than ``chat_messages`` because the
        trace is what carries the outcome, latency and the rating join — the
        three things a reviewer opens a conversation to see.
        """
        rating = (
            select(
                AgentFeedback.trace_id.label("trace_id"),
                func.max(AgentFeedback.score).label("score"),
                func.max(AgentFeedback.comment).label("comment"),
                func.max(AgentFeedback.source).label("source"),
            )
            .where(AgentFeedback.feedback_type == _USER_RATING)
            .group_by(AgentFeedback.trace_id)
            .subquery()
        )

        rows = await self._session.execute(
            select(
                AgentTrace.id,
                AgentTrace.created_at,
                AgentTrace.user_id,
                AgentTrace.input_message,
                AgentTrace.output_message,
                AgentTrace.active_agent,
                AgentTrace.intent,
                AgentTrace.status,
                AgentTrace.answer_status,
                AgentTrace.total_latency_ms,
                AgentTrace.error_message,
                rating.c.score,
                rating.c.comment,
                rating.c.source,
            )
            .outerjoin(rating, rating.c.trace_id == AgentTrace.id)
            .where(AgentTrace.session_id == session_id)
            .order_by(AgentTrace.created_at)
        )

        turns: list[dict[str, Any]] = []
        for r in rows:
            question = (r.input_message or "").strip()
            turns.append(
                {
                    "trace_id": str(r.id),
                    "at": r.created_at,
                    "user_id": str(r.user_id),
                    "question": question or None,
                    # A blank question means a background sync, not a user turn.
                    "is_silent": question.lower() in _NON_QUESTION_INPUTS,
                    "answer": r.output_message,
                    "agent": r.active_agent,
                    "agent_label": agent_display_name(r.active_agent, r.intent),
                    "intent": r.intent,
                    "status": r.status,
                    "answer_status": r.answer_status,
                    "latency_ms": r.total_latency_ms,
                    "error": r.error_message,
                    "rating": r.score,
                    "comment": r.comment,
                    "rating_source": r.source,
                }
            )
        return turns

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    async def feedback_comments(
        self,
        filters: AnalyticsFilters,
        *,
        max_score: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Ratings that carry a written comment, newest first."""
        clauses: list[Any] = [
            AgentFeedback.feedback_type == _USER_RATING,
            AgentFeedback.comment.is_not(None),
        ]
        if filters.from_date is not None:
            clauses.append(AgentFeedback.created_at >= filters.from_date)
        if filters.to_date is not None:
            clauses.append(AgentFeedback.created_at <= filters.to_date)
        if filters.user_id is not None:
            clauses.append(AgentFeedback.user_id == filters.user_id)
        if max_score is not None:
            clauses.append(AgentFeedback.score <= max_score)

        total_result = await self._session.execute(
            select(func.count(AgentFeedback.id)).where(*clauses)
        )
        total = total_result.scalar_one() or 0

        rows = await self._session.execute(
            select(
                AgentFeedback.id,
                AgentFeedback.trace_id,
                AgentFeedback.session_id,
                AgentFeedback.user_id,
                AgentFeedback.score,
                AgentFeedback.comment,
                AgentFeedback.source,
                AgentFeedback.sr_id,
                AgentFeedback.created_at,
                AgentTrace.input_message,
                AgentTrace.active_agent,
            )
            .outerjoin(AgentTrace, AgentTrace.id == AgentFeedback.trace_id)
            .where(*clauses)
            .order_by(AgentFeedback.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = [
            {
                "id": str(r.id),
                "trace_id": str(r.trace_id),
                "session_id": str(r.session_id) if r.session_id else None,
                "user_id": str(r.user_id) if r.user_id else None,
                "rating": r.score,
                "comment": r.comment,
                "source": r.source,
                "sr_id": r.sr_id,
                "agent": r.active_agent,
                "question": r.input_message,
                "created_at": r.created_at,
            }
            for r in rows
        ]
        return items, total

    # ------------------------------------------------------------------
    # Engagement / time spent
    # ------------------------------------------------------------------

    async def engagement(self, filters: AnalyticsFilters) -> dict[str, Any]:
        """Session duration and turns-per-session, from message timestamps.

        Duration is wall-clock (last message − first message) per session, so a
        one-turn session correctly reports ~0 rather than being dropped.
        """
        clauses: list[Any] = []
        if filters.from_date is not None:
            clauses.append(ChatMessage.created_at >= filters.from_date)
        if filters.to_date is not None:
            clauses.append(ChatMessage.created_at <= filters.to_date)

        per_session = (
            select(
                ChatMessage.session_id.label("session_id"),
                func.min(ChatMessage.created_at).label("first_at"),
                func.max(ChatMessage.created_at).label("last_at"),
                func.count(ChatMessage.id)
                .filter(
                    ChatMessage.role == "user",
                    func.lower(
                        func.trim(func.coalesce(ChatMessage.content, ""))
                    ).notin_(_NON_QUESTION_INPUTS),
                )
                .label("user_turns"),
            )
            .where(*clauses)
            .group_by(ChatMessage.session_id)
            .subquery()
        )

        duration_seconds = func.extract(
            "epoch", per_session.c.last_at - per_session.c.first_at
        )
        result = await self._session.execute(
            select(
                func.count(per_session.c.session_id).label("sessions"),
                func.avg(duration_seconds).label("avg_seconds"),
                func.percentile_cont(0.5)
                .within_group(duration_seconds.asc())
                .label("median_seconds"),
                func.sum(duration_seconds).label("total_seconds"),
                func.avg(per_session.c.user_turns).label("avg_turns"),
                func.sum(per_session.c.user_turns).label("total_questions"),
            )
        )
        row = result.one()

        sessions = row.sessions or 0
        avg_seconds = float(row.avg_seconds or 0)
        return {
            "sessions": sessions,
            "avg_session_seconds": round(avg_seconds),
            "avg_session_minutes": round(avg_seconds / 60, 2),
            "median_session_seconds": (
                round(float(row.median_seconds)) if row.median_seconds is not None else 0
            ),
            "total_time_spent_minutes": round(float(row.total_seconds or 0) / 60, 2),
            "avg_turns_per_session": round(float(row.avg_turns or 0), 2),
            "total_questions": int(row.total_questions or 0),
        }

    async def hourly_sessions(
        self, filters: AnalyticsFilters
    ) -> list[int]:
        """Distinct sessions per hour-of-day (0–23) across the window."""
        hour = cast(func.extract("hour", AgentTrace.created_at), Integer).label("hour")
        rows = await self._session.execute(
            select(hour, func.count(func.distinct(AgentTrace.session_id)).label("count"))
            .where(*filters.trace_clauses())
            .group_by(hour)
            .order_by(hour)
        )
        buckets = [0] * 24
        for row in rows:
            index = int(row.hour)
            if 0 <= index < 24:
                buckets[index] = row.count or 0
        return buckets

    async def recent_sessions(
        self, filters: AnalyticsFilters, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """Recent conversations with turn counts, outcome and rating."""
        clauses = filters.trace_clauses()

        grouped = (
            select(
                AgentTrace.session_id.label("session_id"),
                func.count(AgentTrace.id).label("turns"),
                func.min(AgentTrace.created_at).label("started_at"),
                func.max(AgentTrace.created_at).label("last_at"),
                func.max(cast(AgentTrace.user_id, Text())).label("user_id"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status.in_(["UNANSWERED", "FAILED"]))
                .label("not_answered"),
                func.max(AgentTrace.active_agent).label("agent"),
                # Names the handler for FAQ and notification turns, which never
                # set active_agent.
                func.max(AgentTrace.intent).label("intent"),
            )
            .where(*clauses)
            .group_by(AgentTrace.session_id)
            .subquery()
        )

        total_result = await self._session.execute(
            select(func.count()).select_from(grouped)
        )
        total = total_result.scalar_one() or 0

        first_message = (
            select(
                ChatMessage.session_id.label("session_id"),
                func.min(ChatMessage.created_at).label("first_at"),
            )
            .where(ChatMessage.role == "user")
            .group_by(ChatMessage.session_id)
            .subquery()
        )

        rating = (
            select(
                AgentFeedback.session_id.label("session_id"),
                func.avg(AgentFeedback.score).label("avg_score"),
                func.count(AgentFeedback.id).label("ratings_count"),
                # What the user actually wrote is the most useful cell in the
                # row — surface it rather than making someone open the trace.
                func.string_agg(
                    AgentFeedback.comment, literal(" | ")
                ).label("comments"),
            )
            .where(
                AgentFeedback.feedback_type == _USER_RATING,
                AgentFeedback.session_id.is_not(None),
            )
            .group_by(AgentFeedback.session_id)
            .subquery()
        )

        created_srs = (
            select(
                ServiceRequestChatAuditLog.session_id.label("session_id"),
                func.count(ServiceRequestChatAuditLog.id).label("created"),
            )
            .where(ServiceRequestChatAuditLog.action == "service_request.created")
            .group_by(ServiceRequestChatAuditLog.session_id)
            .subquery()
        )

        # The opening question, so a conversation row can preview what the
        # user actually asked. Silent refresh turns are skipped — previewing
        # a blank background sync tells the reader nothing.
        opening_question = (
            select(
                AgentTrace.session_id.label("session_id"),
                func.min(AgentTrace.created_at).label("asked_at"),
            )
            .where(_is_real_question())
            .group_by(AgentTrace.session_id)
            .subquery()
        )
        first_question = (
            select(
                opening_question.c.session_id.label("session_id"),
                AgentTrace.input_message.label("question"),
            )
            .join(
                AgentTrace,
                (AgentTrace.session_id == opening_question.c.session_id)
                & (AgentTrace.created_at == opening_question.c.asked_at),
            )
            .subquery()
        )

        rows = await self._session.execute(
            select(
                grouped.c.session_id,
                grouped.c.turns,
                grouped.c.started_at,
                grouped.c.last_at,
                grouped.c.user_id,
                grouped.c.not_answered,
                grouped.c.agent,
                grouped.c.intent,
                first_message.c.first_at,
                rating.c.avg_score,
                rating.c.ratings_count,
                rating.c.comments,
                first_question.c.question,
                func.coalesce(created_srs.c.created, 0).label("srs_created"),
            )
            .select_from(grouped)
            .outerjoin(
                first_message, first_message.c.session_id == grouped.c.session_id
            )
            .outerjoin(rating, rating.c.session_id == grouped.c.session_id)
            .outerjoin(
                first_question, first_question.c.session_id == grouped.c.session_id
            )
            .outerjoin(created_srs, created_srs.c.session_id == grouped.c.session_id)
            .order_by(grouped.c.last_at.desc())
            .limit(limit)
            .offset(offset)
        )

        items: list[dict[str, Any]] = []
        for r in rows:
            duration = (r.last_at - r.started_at).total_seconds() if r.started_at else 0
            items.append(
                {
                    "session_id": str(r.session_id),
                    "user_id": r.user_id,
                    "agent": r.agent,
                    "agent_label": agent_display_name(r.agent, r.intent),
                    "first_question": r.question,
                    "turns": r.turns or 0,
                    "started_at": r.started_at,
                    "last_activity_at": r.last_at,
                    "duration_seconds": round(duration),
                    "unanswered_turns": r.not_answered or 0,
                    "resolved": bool(r.srs_created) or (r.not_answered or 0) == 0,
                    "srs_created": r.srs_created or 0,
                    "avg_rating": (
                        round(float(r.avg_score), 2) if r.avg_score is not None else None
                    ),
                    "ratings_count": r.ratings_count or 0,
                    "comment": r.comments,
                }
            )
        return items, total

    async def agent_breakdown(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        """Per-agent turn volume, answered share and latency."""
        rows = await self._session.execute(
            select(
                AgentTrace.active_agent.label("agent"),
                func.count(AgentTrace.id).label("turns"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.answer_status == "ANSWERED")
                .label("answered"),
                func.count(AgentTrace.id)
                .filter(AgentTrace.status == "FAILED")
                .label("failed"),
                func.avg(AgentTrace.total_latency_ms).label("avg_latency_ms"),
            )
            .where(*filters.trace_clauses())
            .group_by(AgentTrace.active_agent)
            .order_by(func.count(AgentTrace.id).desc())
        )
        breakdown: list[dict[str, Any]] = []
        for r in rows:
            turns = r.turns or 0
            breakdown.append(
                {
                    # Raw name stays the filter key; the label is for display.
                    "agent": r.agent or "unrouted",
                    "agent_label": agent_display_name(r.agent),
                    "turns": turns,
                    "answered": r.answered or 0,
                    "failed": r.failed or 0,
                    "answered_rate": round((r.answered or 0) / turns, 4) if turns else 0.0,
                    "avg_latency_ms": (
                        round(float(r.avg_latency_ms))
                        if r.avg_latency_ms is not None
                        else 0
                    ),
                }
            )
        return breakdown
