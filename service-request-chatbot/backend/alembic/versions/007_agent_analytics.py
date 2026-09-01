"""Agent product analytics — session clocks, feedback extras, events (ticket 3713).

Revision ID: 007
Revises: 006
Create Date: 2026-09-01

Adds:
  - chat_sessions.last_activity_at / last_heartbeat_at / ended_at /
    end_reason / client_active_ms
  - agent_feedback.session_id / sr_id / metadata; trace_id nullable
  - analytics_events append-only product log
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("end_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column(
            "client_active_ms",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        "UPDATE chat_sessions SET last_activity_at = COALESCE(updated_at, created_at)"
    )
    op.alter_column(
        "chat_sessions",
        "last_activity_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    op.create_index(
        "ix_chat_sessions_last_activity_at",
        "chat_sessions",
        ["last_activity_at"],
    )

    op.alter_column(
        "agent_feedback",
        "trace_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column(
        "agent_feedback",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_feedback",
        sa.Column("sr_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_feedback",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index("ix_agent_feedback_session_id", "agent_feedback", ["session_id"])
    op.create_index("ix_agent_feedback_user_id", "agent_feedback", ["user_id"])
    op.create_check_constraint(
        "ck_agent_feedback_target",
        "agent_feedback",
        "trace_id IS NOT NULL OR session_id IS NOT NULL",
    )

    op.create_table(
        "analytics_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "ix_analytics_events_session_id", "analytics_events", ["session_id"]
    )
    op.create_index(
        "ix_analytics_events_user_occurred",
        "analytics_events",
        ["user_id", "occurred_at"],
    )
    op.create_index(
        "ix_analytics_events_name_occurred",
        "analytics_events",
        ["event_name", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_analytics_events_name_occurred", table_name="analytics_events")
    op.drop_index("ix_analytics_events_user_occurred", table_name="analytics_events")
    op.drop_index("ix_analytics_events_session_id", table_name="analytics_events")
    op.drop_table("analytics_events")

    op.drop_constraint("ck_agent_feedback_target", "agent_feedback", type_="check")
    op.drop_index("ix_agent_feedback_user_id", table_name="agent_feedback")
    op.drop_index("ix_agent_feedback_session_id", table_name="agent_feedback")
    op.drop_column("agent_feedback", "metadata")
    op.drop_column("agent_feedback", "sr_id")
    op.drop_column("agent_feedback", "session_id")
    op.alter_column(
        "agent_feedback",
        "trace_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.drop_index("ix_chat_sessions_last_activity_at", table_name="chat_sessions")
    op.drop_column("chat_sessions", "client_active_ms")
    op.drop_column("chat_sessions", "end_reason")
    op.drop_column("chat_sessions", "ended_at")
    op.drop_column("chat_sessions", "last_heartbeat_at")
    op.drop_column("chat_sessions", "last_activity_at")
