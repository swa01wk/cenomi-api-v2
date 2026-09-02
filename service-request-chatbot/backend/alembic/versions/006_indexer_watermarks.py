"""indexer_watermarks — per-source incremental indexing state (ticket 3547).

Revision ID: 006
Revises: 005
Create Date: 2026-08-19

Context
-------
The RAG knowledge indexer (``app.indexer``) detects change in the Cenomi
platform database via ``updated_at`` watermarks.  This table — the ONLY thing
the indexer stores in the chatbot database — records, per source_type:

  - ``watermark_at``   the change-detection cursor (advanced only on success)
  - ``last_run_at``    stamped at run start so stuck jobs are visible
  - ``last_status``    running | success | failed
  - ``docs_upserted`` / ``docs_deleted``  last successful run's counts
  - ``error``          last failure message (truncated)

No Cenomi content rows are ever copied here.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "indexer_watermarks",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("watermark_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_status", sa.Text(), nullable=False),
        sa.Column(
            "docs_upserted", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "docs_deleted", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("indexer_watermarks")
