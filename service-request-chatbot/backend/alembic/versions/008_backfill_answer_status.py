"""backfill_answer_status — classify traces recorded before migration 007.

Revision ID: 008
Revises: 007
Create Date: 2026-09-02

Why
---
``answer_status`` (007) is only written by the orchestration layer going
forward, so every trace recorded before it shipped is NULL.  The analytics
layer computes the answered ratio over classified traces only, which made the
dashboard report "100% accuracy" from a sample of 2 while 2,668 questions sat
unclassified — a number that reads as coverage it does not have.

What this can and cannot recover
--------------------------------
Retroactive classification uses only what was persisted: the trace status and
the reply text.  That is enough to separate failures and known fallback
replies from real answers:

    FAILED      the trace failed outright
    UNANSWERED  no reply text, or the FAQ / response-generation fallback,
                or the supervisor never resolved an intent (UNKNOWN)
    ANSWERED    everything else

PARTIAL is deliberately never assigned here.  It means "answered without
retrieval grounding", and whether RAG returned context was not persisted per
trace — inventing that distinction would be guessing.  Historical rows are
therefore coarser than new ones, which is recorded in ``metadata`` so a
reader can tell the two apart.

Reversible: the downgrade clears only the rows this migration stamped.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

# Kept verbatim from faq_node._FALLBACK_MESSAGE and
# response_generation_node._FALLBACK_RESPONSE at the time of writing.  Copied
# rather than imported so the migration keeps classifying historical rows the
# same way even after the app's wording changes.
_FAQ_FALLBACK = (
    "I'm here to help with questions about the platform and service requests. "
    "Could you rephrase your question?"
)
_RESPONSE_FALLBACK = (
    "I'm processing your request. Please wait a moment or provide more details."
)

_BACKFILL_SQL = """
    UPDATE agent_traces
    SET answer_status = CASE
            WHEN status = 'FAILED' THEN 'FAILED'
            WHEN COALESCE(TRIM(output_message), '') = '' THEN 'UNANSWERED'
            WHEN TRIM(output_message) = :faq_fallback THEN 'UNANSWERED'
            WHEN TRIM(output_message) = :response_fallback THEN 'UNANSWERED'
            WHEN UPPER(COALESCE(intent, '')) = 'UNKNOWN' THEN 'UNANSWERED'
            ELSE 'ANSWERED'
        END,
        metadata = COALESCE(metadata, '{}'::jsonb)
                   || '{"answer_status_backfilled": true}'::jsonb
    WHERE answer_status IS NULL
"""

_CLEAR_SQL = """
    UPDATE agent_traces
    SET answer_status = NULL,
        metadata = metadata - 'answer_status_backfilled'
    WHERE metadata ->> 'answer_status_backfilled' = 'true'
"""


def upgrade() -> None:
    op.execute(
        sa.text(_BACKFILL_SQL).bindparams(
            faq_fallback=_FAQ_FALLBACK,
            response_fallback=_RESPONSE_FALLBACK,
        )
    )


def downgrade() -> None:
    op.execute(sa.text(_CLEAR_SQL))
