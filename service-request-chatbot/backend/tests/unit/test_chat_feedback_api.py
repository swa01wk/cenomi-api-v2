"""Unit tests for the end-user chat feedback endpoint and its helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes.chat_feedback import (
    ChatFeedbackRequest,
    ChatThumbsRequest,
    set_chat_thumbs,
    submit_chat_feedback,
    thumbs_source,
)
from app.observability.repositories.feedback_repo import FeedbackRepository
from app.db.models import AgentFeedback
from app.services.chat_orchestration_service import (
    _classify_answer_status,
    user_id_to_uuid,
)
from app.types.chat import AuthContext


# ── helpers ────────────────────────────────────────────────────────────────


def _auth(subject: str = "user_42") -> AuthContext:
    return AuthContext(subject_id=subject)


def _request(**overrides: object) -> ChatFeedbackRequest:
    defaults: dict[str, object] = {
        "trace_id": uuid4(),
        "rating": 5,
        "source": "help_answer",
    }
    defaults.update(overrides)
    return ChatFeedbackRequest(**defaults)  # type: ignore[arg-type]


def _feedback_row(trace_id, user_id, session_id) -> AgentFeedback:
    from datetime import datetime, timezone

    return AgentFeedback(
        id=uuid4(),
        trace_id=trace_id,
        user_id=user_id,
        session_id=session_id,
        feedback_type="USER_RATING",
        score=5,
        metadata_={},
        # Set client-side in tests; the DB fills this via server_default.
        created_at=datetime.now(timezone.utc),
    )


# ── endpoint ───────────────────────────────────────────────────────────────


async def test_anonymous_subject_rejected(mock_session: AsyncMock) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await submit_chat_feedback(_request(), mock_session, _auth("anonymous"))
    assert exc_info.value.status_code == 401


async def test_unknown_trace_404(mock_session: AsyncMock) -> None:
    with patch(
        "app.api.routes.chat_feedback.TraceRepository"
    ) as trace_repo_cls:
        trace_repo_cls.return_value.get = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc_info:
            await submit_chat_feedback(_request(), mock_session, _auth())
    assert exc_info.value.status_code == 404


async def test_other_users_trace_403(mock_session: AsyncMock) -> None:
    trace = MagicMock()
    trace.user_id = uuid4()  # not the caller
    with patch(
        "app.api.routes.chat_feedback.TraceRepository"
    ) as trace_repo_cls:
        trace_repo_cls.return_value.get = AsyncMock(return_value=trace)
        with pytest.raises(HTTPException) as exc_info:
            await submit_chat_feedback(_request(), mock_session, _auth())
    assert exc_info.value.status_code == 403


async def test_rating_recorded_with_server_derived_context(
    mock_session: AsyncMock,
) -> None:
    auth = _auth("user_42")
    caller_uuid = user_id_to_uuid(auth.subject_id)
    body = _request(source="sr_created", sr_id="11711", comment="great")

    trace = MagicMock()
    trace.user_id = caller_uuid
    trace.session_id = uuid4()
    row = _feedback_row(body.trace_id, caller_uuid, trace.session_id)

    with (
        patch("app.api.routes.chat_feedback.TraceRepository") as trace_repo_cls,
        patch("app.api.routes.chat_feedback.FeedbackRepository") as fb_repo_cls,
        patch("app.api.routes.chat_feedback.AuditLogRepository") as audit_repo_cls,
    ):
        trace_repo_cls.return_value.get = AsyncMock(return_value=trace)
        upsert = AsyncMock(return_value=(row, True))
        fb_repo_cls.return_value.upsert_user_rating = upsert
        audit_create = AsyncMock()
        audit_repo_cls.return_value.create = audit_create

        response = await submit_chat_feedback(body, mock_session, auth)

    assert response.created is True
    assert response.feedback.feedback_type == "USER_RATING"
    # user_id and session_id come from the JWT and the trace — never the body.
    upsert.assert_awaited_once_with(
        trace_id=body.trace_id,
        user_id=caller_uuid,
        score=5,
        comment="great",
        session_id=trace.session_id,
        message_id=None,
        source="sr_created",
        sr_id="11711",
    )
    audit_create.assert_awaited_once()
    assert audit_create.await_args.kwargs["action"] == "feedback.submitted"


def test_rating_bounds_enforced() -> None:
    with pytest.raises(ValueError):
        _request(rating=0)
    with pytest.raises(ValueError):
        _request(rating=6)
    with pytest.raises(ValueError):
        _request(source="random_place")


# ── thumbs ─────────────────────────────────────────────────────────────────


def _thumbs_row(trace_id, user_id, label: str) -> AgentFeedback:
    row = _feedback_row(trace_id, user_id, uuid4())
    row.feedback_type = "TURN_THUMBS"
    row.score = None
    row.label = label
    return row


def _own_trace(auth: AuthContext, stage: str | None = None) -> MagicMock:
    trace = MagicMock()
    trace.user_id = user_id_to_uuid(auth.subject_id)
    trace.session_id = uuid4()
    trace.workflow_stage_after = stage
    trace.active_agent = "rdd_handover_agent"
    return trace


async def test_thumbs_anonymous_rejected(mock_session: AsyncMock) -> None:
    body = ChatThumbsRequest(trace_id=uuid4(), value="up")
    with pytest.raises(HTTPException) as exc_info:
        await set_chat_thumbs(body, mock_session, _auth("anonymous"))
    assert exc_info.value.status_code == 401


async def test_thumbs_other_users_trace_403(mock_session: AsyncMock) -> None:
    trace = MagicMock()
    trace.user_id = uuid4()
    with patch("app.api.routes.chat_feedback.TraceRepository") as trace_repo_cls:
        trace_repo_cls.return_value.get = AsyncMock(return_value=trace)
        with pytest.raises(HTTPException) as exc_info:
            await set_chat_thumbs(
                ChatThumbsRequest(trace_id=uuid4(), value="down"), mock_session, _auth()
            )
    assert exc_info.value.status_code == 403


async def test_thumbs_unknown_trace_404(mock_session: AsyncMock) -> None:
    with patch("app.api.routes.chat_feedback.TraceRepository") as trace_repo_cls:
        trace_repo_cls.return_value.get = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc_info:
            await set_chat_thumbs(
                ChatThumbsRequest(trace_id=uuid4(), value="up"), mock_session, _auth()
            )
    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    ("value", "label"), [("up", "THUMBS_UP"), ("down", "THUMBS_DOWN"), (None, None)]
)
async def test_thumbs_vote_recorded(
    mock_session: AsyncMock, value: str | None, label: str | None
) -> None:
    auth = _auth()
    trace = _own_trace(auth, stage="COLLECTING_LEASE")
    body = ChatThumbsRequest(trace_id=uuid4(), value=value, message_id=uuid4())
    row = _thumbs_row(body.trace_id, trace.user_id, label) if label else None

    with (
        patch("app.api.routes.chat_feedback.TraceRepository") as trace_repo_cls,
        patch("app.api.routes.chat_feedback.FeedbackRepository") as fb_repo_cls,
        patch("app.api.routes.chat_feedback.AuditLogRepository") as audit_repo_cls,
    ):
        trace_repo_cls.return_value.get = AsyncMock(return_value=trace)
        set_thumbs = AsyncMock(return_value=row)
        fb_repo_cls.return_value.set_turn_thumbs = set_thumbs
        audit_create = AsyncMock()
        audit_repo_cls.return_value.create = audit_create

        response = await set_chat_thumbs(body, mock_session, auth)

    set_thumbs.assert_awaited_once_with(
        trace_id=body.trace_id,
        user_id=trace.user_id,
        label=label,
        session_id=trace.session_id,
        message_id=body.message_id,
        source="sr_step",
        comment=None,
        metadata={"agent": "rdd_handover_agent"},
    )
    assert response.value == value
    assert (response.feedback is None) == (label is None)
    assert audit_create.await_args.kwargs["action"] == "feedback.thumbs"


async def test_thumbs_down_with_reason_and_details(mock_session: AsyncMock) -> None:
    auth = _auth()
    trace = _own_trace(auth)
    body = ChatThumbsRequest(
        trace_id=uuid4(), value="down", reason="NOT_WHAT_I_ASKED", comment="  wrong lease  "
    )

    with (
        patch("app.api.routes.chat_feedback.TraceRepository") as trace_repo_cls,
        patch("app.api.routes.chat_feedback.FeedbackRepository") as fb_repo_cls,
        patch("app.api.routes.chat_feedback.AuditLogRepository") as audit_repo_cls,
    ):
        trace_repo_cls.return_value.get = AsyncMock(return_value=trace)
        set_thumbs = AsyncMock(
            return_value=_thumbs_row(body.trace_id, trace.user_id, "THUMBS_DOWN")
        )
        fb_repo_cls.return_value.set_turn_thumbs = set_thumbs
        audit_repo_cls.return_value.create = AsyncMock()

        await set_chat_thumbs(body, mock_session, auth)

    kwargs = set_thumbs.await_args.kwargs
    assert kwargs["label"] == "THUMBS_DOWN"
    assert kwargs["comment"] == "wrong lease"
    assert kwargs["metadata"] == {"agent": "rdd_handover_agent", "reason": "NOT_WHAT_I_ASKED"}


def test_thumbs_reason_only_with_down() -> None:
    with pytest.raises(ValueError):
        ChatThumbsRequest(trace_id=uuid4(), value="up", reason="OTHER")
    with pytest.raises(ValueError):
        ChatThumbsRequest(trace_id=uuid4(), value=None, comment="hm")
    with pytest.raises(ValueError):
        ChatThumbsRequest(trace_id=uuid4(), value="down", reason="SAFETY_OR_LEGAL")  # type: ignore[arg-type]
    blank = ChatThumbsRequest(trace_id=uuid4(), value="down", comment="   ")
    assert blank.comment is None


def test_thumbs_rejects_other_values() -> None:
    with pytest.raises(ValueError):
        ChatThumbsRequest(trace_id=uuid4(), value="meh")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ChatThumbsRequest(trace_id=uuid4())  # type: ignore[call-arg]


def test_thumbs_source_from_stage() -> None:
    assert thumbs_source(None) == "help_answer"
    assert thumbs_source("") == "help_answer"
    assert thumbs_source("COLLECTING_LEASE") == "sr_step"
    assert thumbs_source("SR_CREATED") == "sr_created"
    assert thumbs_source("HANDED_OVER_COMPLETED") == "sr_created"


async def test_repo_thumbs_vote_is_one_upsert(mock_session: AsyncMock) -> None:
    repo = FeedbackRepository(mock_session)
    trace_id, user_id = uuid4(), uuid4()
    row = _thumbs_row(trace_id, user_id, "THUMBS_UP")
    result = MagicMock()
    result.scalar_one.return_value = row
    mock_session.execute = AsyncMock(return_value=result)

    stored = await repo.set_turn_thumbs(
        trace_id=trace_id, user_id=user_id, label="THUMBS_UP", source="help_answer"
    )

    assert stored is row
    mock_session.execute.assert_awaited_once()
    sql = str(
        mock_session.execute.await_args.args[0].compile(
            dialect=__import__("sqlalchemy.dialects.postgresql", fromlist=["dialect"]).dialect()
        )
    )
    assert "ON CONFLICT (trace_id, user_id) WHERE feedback_type = 'TURN_THUMBS'" in sql
    assert "label = excluded.label" in sql
    assert "comment = excluded.comment" in sql
    assert "metadata = excluded.metadata" in sql
    mock_session.add.assert_not_called()


async def test_repo_thumbs_clear_deletes(mock_session: AsyncMock) -> None:
    repo = FeedbackRepository(mock_session)
    mock_session.execute = AsyncMock()

    cleared = await repo.set_turn_thumbs(trace_id=uuid4(), user_id=uuid4(), label=None)

    assert cleared is None
    statement = mock_session.execute.await_args.args[0]
    assert statement.is_delete


# ── answer-status classifier ───────────────────────────────────────────────


def test_classifier_prefers_explicit_node_verdict() -> None:
    state = {"answer_status": "UNANSWERED", "intent": "ASK_HELP"}
    assert _classify_answer_status(state, "WAITING_FOR_USER") == "UNANSWERED"


def test_classifier_unknown_intent_is_unanswered() -> None:
    assert (
        _classify_answer_status({"intent": "UNKNOWN"}, "WAITING_FOR_USER")
        == "UNANSWERED"
    )


def test_classifier_failed_status() -> None:
    assert _classify_answer_status({}, "FAILED") == "FAILED"


def test_classifier_defaults_to_answered() -> None:
    assert (
        _classify_answer_status(
            {"intent": "CREATE_SERVICE_REQUEST"}, "WAITING_FOR_USER"
        )
        == "ANSWERED"
    )


def test_classifier_ignores_invalid_explicit_value() -> None:
    state = {"answer_status": "SOMETHING_ELSE", "intent": "ASK_HELP"}
    assert _classify_answer_status(state, "WAITING_FOR_USER") == "ANSWERED"
