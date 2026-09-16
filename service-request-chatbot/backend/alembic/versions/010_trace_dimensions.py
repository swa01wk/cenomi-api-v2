"""Segmentation dimensions on agent_traces (ticket 3784).

Revision ID: 010
Revises: 009
Create Date: 2026-09-16

Stamps who asked and from where on every trace so usage can be segmented by
mall, role and channel.  Written from the JWT claims already on
``AuthContext``; the read side (filters, breakdowns) ships separately.

  - agent_traces.channel          TEXT, indexed
  - agent_traces.user_roles       JSONB
  - agent_traces.mall_names       JSONB
  - agent_traces.property_ids     JSONB
  - agent_traces.is_global_admin  BOOLEAN

All nullable: historic traces and anonymous turns carry no claims.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_traces", sa.Column("channel", sa.Text(), nullable=True))
    op.add_column(
        "agent_traces",
        sa.Column("user_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "agent_traces",
        sa.Column("mall_names", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "agent_traces",
        sa.Column("property_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "agent_traces", sa.Column("is_global_admin", sa.Boolean(), nullable=True)
    )
    op.create_index("ix_agent_traces_channel", "agent_traces", ["channel"])


def downgrade() -> None:
    op.drop_index("ix_agent_traces_channel", table_name="agent_traces")
    op.drop_column("agent_traces", "is_global_admin")
    op.drop_column("agent_traces", "property_ids")
    op.drop_column("agent_traces", "mall_names")
    op.drop_column("agent_traces", "user_roles")
    op.drop_column("agent_traces", "channel")
