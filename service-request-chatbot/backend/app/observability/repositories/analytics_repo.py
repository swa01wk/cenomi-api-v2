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
from sqlalchemy import Integer, Text, and_, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.agent_display import agent_display_name
from app.observability.display_labels import (
    node_label,
    prompt_label,
    stage_label,
    tool_label,
)

from app.db.models import (
    AgentFeedback,
    AgentLLMCall,
    AgentRun,
    AgentToolCall,
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
    """SQL clause matching traces whose input is text a user actually typed."""
    return func.lower(func.trim(func.coalesce(AgentTrace.input_message, ""))).notin_(
        _NON_QUESTION_INPUTS
    )


def _is_standalone_question() -> Any:
    """SQL clause matching turns that are questions rather than form input.

    Once a service-request workflow is running, the user's turns are answers
    to the agent's prompts — a lease code, a date, "yes, please submit". They
    are real turns and still count toward volume and accuracy, but they are
    not questions, and counting them buries the actual questions: on live data
    "20/08/2026" and "t0111600" outranked everything a user genuinely asked.

    ``workflow_stage_before IS NULL`` separates the two exactly. A help
    question and the utterance that starts a workflow both arrive with no
    stage in progress; every field response arrives with one.
    """
    return and_(_is_real_question(), AgentTrace.workflow_stage_before.is_(None))


@dataclass(frozen=True, slots=True)
class AnalyticsFilters:
    """Common filter set accepted by every analytics endpoint."""

    from_date: datetime | None = None
    to_date: datetime | None = None
    agent: str | None = None
    user_id: UUID | None = None

    def scoped_trace_ids(self) -> Any | None:
        """Sub-select of trace ids matching agent/user, or None when unscoped.

        Tables hanging off a trace (runs, tool and LLM calls, feedback) carry
        no agent or user of their own; they are scoped through their trace.
        """
        if self.agent is None and self.user_id is None:
            return None
        clauses: list[Any] = []
        if self.agent is not None:
            clauses.append(AgentTrace.active_agent == self.agent)
        if self.user_id is not None:
            clauses.append(AgentTrace.user_id == self.user_id)
        return select(AgentTrace.id).where(*clauses)

    def scoped_session_ids(self) -> Any | None:
        """Sub-select of session ids the agent/user filter touches, or None."""
        if self.agent is None and self.user_id is None:
            return None
        clauses: list[Any] = []
        if self.agent is not None:
            clauses.append(AgentTrace.active_agent == self.agent)
        if self.user_id is not None:
            clauses.append(AgentTrace.user_id == self.user_id)
        return select(AgentTrace.session_id).where(*clauses)

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
        questions_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Question log with each turn's outcome and rating, newest first."""
        clauses = filters.trace_clauses()
        if answer_status is not None:
            clauses.append(AgentTrace.answer_status == answer_status)
        if search:
            clauses.append(AgentTrace.input_message.ilike(f"%{search}%"))
        # questions_only drops mid-workflow form input (a date, a lease code,
        # "yes"), leaving what users actually asked.
        clauses.append(
            _is_standalone_question() if questions_only else _is_real_question()
        )

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
            .where(*filters.trace_clauses(), _is_standalone_question())
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
        if filters.agent is not None:
            clauses.append(
                AgentFeedback.trace_id.in_(
                    select(AgentTrace.id).where(AgentTrace.active_agent == filters.agent)
                )
            )
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
        scoped_sessions = filters.scoped_session_ids()
        if scoped_sessions is not None:
            clauses.append(ChatMessage.session_id.in_(scoped_sessions))

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
        self,
        filters: AnalyticsFilters,
        *,
        search: str | None = None,
        user_ids: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Recent conversations with turn counts, outcome and rating.

        ``search`` matches the three things the list actually shows: the
        session id, the questions asked, and — via *user_ids*, resolved from
        the platform directory by the caller — the person's name. Names are
        not stored in these tables, so they cannot be matched in SQL alone.
        """
        clauses = filters.trace_clauses()

        needle = (search or "").strip()
        if needle or user_ids:
            alternatives: list[Any] = []
            if needle:
                pattern = f"%{needle}%"
                alternatives.append(
                    cast(AgentTrace.session_id, Text()).ilike(pattern)
                )
                alternatives.append(
                    AgentTrace.session_id.in_(
                        select(AgentTrace.session_id).where(
                            AgentTrace.input_message.ilike(pattern)
                        )
                    )
                )
            if user_ids:
                alternatives.append(AgentTrace.user_id.in_(user_ids))
            clauses.append(or_(*alternatives))

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

        # Which agent handled the conversation. A session can move between
        # agents (a help question that turns into a service request), so this
        # takes the agent it ended on rather than an aggregate — MAX() over a
        # text column picks alphabetically, which is arbitrary.
        latest_turn = (
            select(
                AgentTrace.session_id.label("session_id"),
                func.max(AgentTrace.created_at).label("last_at"),
            )
            .where(*clauses)
            .group_by(AgentTrace.session_id)
            .subquery()
        )
        final_agent = (
            select(
                latest_turn.c.session_id.label("session_id"),
                AgentTrace.active_agent.label("agent"),
                AgentTrace.intent.label("intent"),
            )
            .join(
                AgentTrace,
                (AgentTrace.session_id == latest_turn.c.session_id)
                & (AgentTrace.created_at == latest_turn.c.last_at),
            )
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
                final_agent.c.agent,
                final_agent.c.intent,
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
                final_agent, final_agent.c.session_id == grouped.c.session_id
            )
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

    async def top_users(
        self, filters: AnalyticsFilters, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Busiest users, with how the assistant is rated by each.

        Volume alone says who to talk to; volume beside a rating says which of
        them is being let down. Ranked by questions asked — the rating is the
        second column precisely because a heavy user with a poor average is
        the most useful row on the page.
        """
        rating = (
            select(
                AgentFeedback.user_id.label("user_id"),
                func.avg(AgentFeedback.score).label("avg_score"),
                func.count(AgentFeedback.id).label("ratings_count"),
            )
            .where(
                AgentFeedback.feedback_type == _USER_RATING,
                AgentFeedback.user_id.is_not(None),
            )
            .group_by(AgentFeedback.user_id)
            .subquery()
        )

        grouped = (
            select(
                AgentTrace.user_id.label("user_id"),
                func.count(AgentTrace.id)
                .filter(_is_real_question())
                .label("questions"),
                func.count(func.distinct(AgentTrace.session_id)).label("sessions"),
                func.count(AgentTrace.id)
                .filter(
                    AgentTrace.answer_status.in_(["UNANSWERED", "FAILED"]),
                    _is_real_question(),
                )
                .label("unanswered"),
                func.max(AgentTrace.created_at).label("last_active_at"),
            )
            .where(*filters.trace_clauses())
            .group_by(AgentTrace.user_id)
            .subquery()
        )

        rows = await self._session.execute(
            select(
                grouped.c.user_id,
                grouped.c.questions,
                grouped.c.sessions,
                grouped.c.unanswered,
                grouped.c.last_active_at,
                rating.c.avg_score,
                rating.c.ratings_count,
            )
            .select_from(grouped)
            .outerjoin(rating, rating.c.user_id == grouped.c.user_id)
            # Users whose turns were all silent syncs are not people to report on.
            .where(grouped.c.questions > 0)
            .order_by(grouped.c.questions.desc())
            .limit(limit)
        )

        return [
            {
                "user_id": str(r.user_id),
                "questions": r.questions or 0,
                "sessions": r.sessions or 0,
                "unanswered": r.unanswered or 0,
                "last_active_at": r.last_active_at,
                "avg_rating": (
                    round(float(r.avg_score), 2) if r.avg_score is not None else None
                ),
                "ratings_count": r.ratings_count or 0,
            }
            for r in rows
        ]

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

    # ------------------------------------------------------------------
    # Operational insights (agent_runs / agent_tool_calls / agent_llm_calls)
    # ------------------------------------------------------------------

    async def node_latency(
        self, filters: AnalyticsFilters, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Slowest graph nodes — where a turn's time actually goes.

        Ranked by p95 rather than the mean: a node that is usually fast but
        occasionally stalls is the one users notice, and averaging hides it.
        """
        clauses: list[Any] = [AgentRun.latency_ms.is_not(None)]
        if filters.from_date is not None:
            clauses.append(AgentRun.started_at >= filters.from_date)
        if filters.to_date is not None:
            clauses.append(AgentRun.started_at <= filters.to_date)
        scoped_traces = filters.scoped_trace_ids()
        if scoped_traces is not None:
            clauses.append(AgentRun.trace_id.in_(scoped_traces))

        rows = await self._session.execute(
            select(
                AgentRun.run_name.label("node"),
                func.count(AgentRun.id).label("calls"),
                func.avg(AgentRun.latency_ms).label("avg_ms"),
                func.percentile_cont(0.95)
                .within_group(AgentRun.latency_ms.asc())
                .label("p95_ms"),
                func.max(AgentRun.latency_ms).label("max_ms"),
                func.count(AgentRun.id)
                .filter(AgentRun.status == "FAILED")
                .label("failed"),
            )
            .where(*clauses)
            .group_by(AgentRun.run_name)
            .order_by(
                func.percentile_cont(0.95)
                .within_group(AgentRun.latency_ms.asc())
                .desc()
            )
            .limit(limit)
        )
        return [
            {
                "node": r.node,
                "node_label": node_label(r.node),
                "calls": r.calls or 0,
                "avg_ms": round(float(r.avg_ms)) if r.avg_ms is not None else 0,
                "p95_ms": round(float(r.p95_ms)) if r.p95_ms is not None else 0,
                "max_ms": r.max_ms or 0,
                "failed": r.failed or 0,
            }
            for r in rows
        ]

    async def tool_health(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        """Platform API calls the agent makes: reliability and latency.

        When a workflow stalls the cause is usually here rather than in the
        model, so failures are surfaced per endpoint.
        """
        clauses: list[Any] = []
        if filters.from_date is not None:
            clauses.append(AgentToolCall.created_at >= filters.from_date)
        if filters.to_date is not None:
            clauses.append(AgentToolCall.created_at <= filters.to_date)
        scoped_traces = filters.scoped_trace_ids()
        if scoped_traces is not None:
            clauses.append(AgentToolCall.trace_id.in_(scoped_traces))

        rows = await self._session.execute(
            select(
                AgentToolCall.tool_name.label("tool"),
                func.count(AgentToolCall.id).label("calls"),
                func.count(AgentToolCall.id)
                .filter(AgentToolCall.success.is_(False))
                .label("failed"),
                func.avg(AgentToolCall.latency_ms).label("avg_ms"),
                func.percentile_cont(0.95)
                .within_group(AgentToolCall.latency_ms.asc())
                .label("p95_ms"),
            )
            .where(*clauses)
            .group_by(AgentToolCall.tool_name)
            .order_by(func.count(AgentToolCall.id).desc())
        )
        health: list[dict[str, Any]] = []
        for r in rows:
            calls = r.calls or 0
            failed = r.failed or 0
            health.append(
                {
                    "tool": r.tool,
                    "tool_label": tool_label(r.tool),
                    "calls": calls,
                    "failed": failed,
                    "success_rate": round((calls - failed) / calls, 4) if calls else 0.0,
                    "avg_ms": round(float(r.avg_ms)) if r.avg_ms is not None else 0,
                    "p95_ms": round(float(r.p95_ms)) if r.p95_ms is not None else 0,
                }
            )
        return health

    async def llm_usage(self, filters: AnalyticsFilters) -> dict[str, Any]:
        """Token spend and reliability per prompt and model.

        ``cost_coverage`` is reported alongside the cost because pricing is not
        known for every model — an unpriced call records no cost, so a total
        without its coverage understates spend silently.
        """
        clauses: list[Any] = []
        if filters.from_date is not None:
            clauses.append(AgentLLMCall.created_at >= filters.from_date)
        if filters.to_date is not None:
            clauses.append(AgentLLMCall.created_at <= filters.to_date)
        scoped_traces = filters.scoped_trace_ids()
        if scoped_traces is not None:
            clauses.append(AgentLLMCall.trace_id.in_(scoped_traces))

        rows = await self._session.execute(
            select(
                func.coalesce(AgentLLMCall.prompt_name, "unnamed").label("prompt"),
                func.coalesce(AgentLLMCall.model, "unknown").label("model"),
                func.count(AgentLLMCall.id).label("calls"),
                func.coalesce(func.sum(AgentLLMCall.total_tokens), 0).label("tokens"),
                func.avg(AgentLLMCall.total_tokens).label("avg_tokens"),
                func.avg(AgentLLMCall.latency_ms).label("avg_ms"),
                func.count(AgentLLMCall.id)
                .filter(AgentLLMCall.parse_success.is_(False))
                .label("parse_failures"),
                func.coalesce(func.sum(AgentLLMCall.estimated_cost), 0).label("cost"),
                func.count(AgentLLMCall.id)
                .filter(AgentLLMCall.estimated_cost.is_not(None))
                .label("priced"),
            )
            .where(*clauses)
            .group_by("prompt", "model")
            .order_by(func.coalesce(func.sum(AgentLLMCall.total_tokens), 0).desc())
        )

        by_prompt: list[dict[str, Any]] = []
        total_tokens = 0
        total_calls = 0
        total_priced = 0
        for r in rows:
            tokens = int(r.tokens or 0)
            calls = r.calls or 0
            total_tokens += tokens
            total_calls += calls
            total_priced += r.priced or 0
            by_prompt.append(
                {
                    "prompt": r.prompt,
                    "prompt_label": prompt_label(r.prompt),
                    "model": r.model,
                    "calls": calls,
                    "tokens": tokens,
                    "avg_tokens": round(float(r.avg_tokens)) if r.avg_tokens else 0,
                    "avg_ms": round(float(r.avg_ms)) if r.avg_ms is not None else 0,
                    "parse_failures": r.parse_failures or 0,
                    "parse_failure_rate": (
                        round((r.parse_failures or 0) / calls, 4) if calls else 0.0
                    ),
                    "cost": float(r.cost or 0),
                    "priced_calls": r.priced or 0,
                }
            )

        # Share of total tokens, so the dominant prompt is obvious.
        for entry in by_prompt:
            entry["token_share"] = (
                round(entry["tokens"] / total_tokens, 4) if total_tokens else 0.0
            )

        return {
            "by_prompt": by_prompt,
            "total_calls": total_calls,
            "total_tokens": total_tokens,
            "cost_coverage": (
                round(total_priced / total_calls, 4) if total_calls else 0.0
            ),
        }

    async def workflow_funnel(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        """Sessions that reached each workflow stage — where SR flows stall."""
        rows = await self._session.execute(
            select(
                AgentTrace.workflow_stage_after.label("stage"),
                func.count(func.distinct(AgentTrace.session_id)).label("sessions"),
                func.count(AgentTrace.id).label("turns"),
            )
            .where(*filters.trace_clauses(), AgentTrace.workflow_stage_after.is_not(None))
            .group_by(AgentTrace.workflow_stage_after)
            .order_by(func.count(func.distinct(AgentTrace.session_id)).desc())
        )
        return [
            {
                "stage": r.stage,
                "stage_label": stage_label(r.stage),
                "sessions": r.sessions or 0,
                "turns": r.turns or 0,
            }
            for r in rows
        ]
