"""One thumbs vote per user per turn (ticket 3784).

Revision ID: 011
Revises: 010
Create Date: 2026-09-17

Every assistant reply in the tenant widget now carries 👍 / 👎.  A vote is
changed or cleared in place, so ``agent_feedback`` holds at most one
TURN_THUMBS row per (trace, user) — the same rule 007 set for USER_RATING.
Duplicates written by the older observability route are collapsed first,
keeping each user's latest vote.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM agent_feedback f
        USING (
            SELECT id, row_number() OVER (
                PARTITION BY trace_id, user_id ORDER BY created_at DESC, id DESC
            ) AS rn
            FROM agent_feedback
            WHERE feedback_type = 'TURN_THUMBS'
              AND trace_id IS NOT NULL AND user_id IS NOT NULL
        ) d
        WHERE f.id = d.id AND d.rn > 1
        """
    )
    op.create_index(
        "ux_agent_feedback_turn_thumbs",
        "agent_feedback",
        ["trace_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("feedback_type = 'TURN_THUMBS'"),
    )


def downgrade() -> None:
    op.drop_index("ux_agent_feedback_turn_thumbs", table_name="agent_feedback")
