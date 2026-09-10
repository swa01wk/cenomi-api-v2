"""feedback_analytics — end-user ratings + turn outcome classification.

Revision ID: 007
Revises: 006
Create Date: 2026-09-02

Context
-------
End users rate agent turns (stars 1–5 + comment) under every help answer and
once per created SR.  ``agent_feedback`` grows the columns needed to query
those ratings without joining through traces, plus a partial unique index so
one user holds at most one USER_RATING per turn (re-submitting updates it).

``agent_traces.answer_status`` classifies each turn's outcome
(ANSWERED / PARTIAL / UNANSWERED / FAILED) so the analytics layer can report
answered-vs-unanswered ratios without re-deriving them from message text.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── agent_feedback: end-user rating context ────────────────────────────
    op.add_column(
        "agent_feedback",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_feedback",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("agent_feedback", sa.Column("source", sa.Text(), nullable=True))
    op.add_column("agent_feedback", sa.Column("sr_id", sa.Text(), nullable=True))
    op.add_column(
        "agent_feedback",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.create_index("ix_agent_feedback_session_id", "agent_feedback", ["session_id"])
    op.create_index("ix_agent_feedback_user_id", "agent_feedback", ["user_id"])
    # One end-user rating per (trace, user); ADMIN_REVIEW etc. stay unlimited.
    op.create_index(
        "ux_agent_feedback_user_rating",
        "agent_feedback",
        ["trace_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("feedback_type = 'USER_RATING'"),
    )

    # ── agent_traces: turn outcome classification ──────────────────────────
    op.add_column("agent_traces", sa.Column("answer_status", sa.Text(), nullable=True))
    op.create_index("ix_agent_traces_answer_status", "agent_traces", ["answer_status"])


def downgrade() -> None:
    op.drop_index("ix_agent_traces_answer_status", table_name="agent_traces")
    op.drop_column("agent_traces", "answer_status")

    op.drop_index("ux_agent_feedback_user_rating", table_name="agent_feedback")
    op.drop_index("ix_agent_feedback_user_id", table_name="agent_feedback")
    op.drop_index("ix_agent_feedback_session_id", table_name="agent_feedback")
    op.drop_column("agent_feedback", "metadata")
    op.drop_column("agent_feedback", "sr_id")
    op.drop_column("agent_feedback", "source")
    op.drop_column("agent_feedback", "message_id")
    op.drop_column("agent_feedback", "session_id")
