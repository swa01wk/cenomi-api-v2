"""Unit tests for the end-user chat feedback endpoint and its helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes.chat_feedback import (
    ChatFeedbackRequest,
    submit_chat_feedback,
)
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
