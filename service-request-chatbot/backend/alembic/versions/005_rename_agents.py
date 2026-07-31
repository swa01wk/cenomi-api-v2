"""Rename agent identifiers: rdd_agent → rdd_handover_agent, fitout_confirm_agent → 3 agents.

Revision ID: 005
Revises: 004
Create Date: 2026-07-29

Context
-------
The agent taxonomy was updated so that:
  - ``rdd_agent``            becomes ``rdd_handover_agent``
  - ``fitout_confirm_agent`` is split into three dedicated agents:
      - ``rdd_fitout_start_agent``       (sub_category = FIT_OUT_CONFIRM_FOS)
      - ``rdd_fitout_end_agent``         (sub_category = FIT_OUT_CONFIRM_END)
      - ``rdd_trading_confirmation_agent`` (sub_category = FIT_OUT_CONFIRM_TRADING)

The ``active_agent`` column in ``chat_sessions`` and ``agent_traces`` stores
the agent name as a plain text value.  The fitout_confirm_agent split requires
joining on ``sub_category`` to route each row to the correct new agent name.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # rdd_agent → rdd_handover_agent
    op.execute(
        "UPDATE chat_sessions "
        "SET active_agent = 'rdd_handover_agent' "
        "WHERE active_agent = 'rdd_agent'"
    )
    op.execute(
        "UPDATE agent_traces "
        "SET active_agent = 'rdd_handover_agent' "
        "WHERE active_agent = 'rdd_agent'"
    )

    # fitout_confirm_agent → rdd_fitout_start_agent (FIT_OUT_CONFIRM_FOS)
    # chat_sessions has no sub_category — join via service_request_drafts
    op.execute(
        "UPDATE chat_sessions "
        "SET active_agent = 'rdd_fitout_start_agent' "
        "WHERE active_agent = 'fitout_confirm_agent' "
        "AND id IN ("
        "  SELECT session_id FROM service_request_drafts"
        "  WHERE sub_category = 'FIT_OUT_CONFIRM_FOS'"
        ")"
    )
    op.execute(
        "UPDATE agent_traces "
        "SET active_agent = 'rdd_fitout_start_agent' "
        "WHERE active_agent = 'fitout_confirm_agent' "
        "AND sub_category = 'FIT_OUT_CONFIRM_FOS'"
    )

    # fitout_confirm_agent → rdd_fitout_end_agent (FIT_OUT_CONFIRM_END)
    op.execute(
        "UPDATE chat_sessions "
        "SET active_agent = 'rdd_fitout_end_agent' "
        "WHERE active_agent = 'fitout_confirm_agent' "
        "AND id IN ("
        "  SELECT session_id FROM service_request_drafts"
        "  WHERE sub_category = 'FIT_OUT_CONFIRM_END'"
        ")"
    )
    op.execute(
        "UPDATE agent_traces "
        "SET active_agent = 'rdd_fitout_end_agent' "
        "WHERE active_agent = 'fitout_confirm_agent' "
        "AND sub_category = 'FIT_OUT_CONFIRM_END'"
    )

    # fitout_confirm_agent → rdd_trading_confirmation_agent (FIT_OUT_CONFIRM_TRADING)
    op.execute(
        "UPDATE chat_sessions "
        "SET active_agent = 'rdd_trading_confirmation_agent' "
        "WHERE active_agent = 'fitout_confirm_agent' "
        "AND id IN ("
        "  SELECT session_id FROM service_request_drafts"
        "  WHERE sub_category = 'FIT_OUT_CONFIRM_TRADING'"
        ")"
    )
    op.execute(
        "UPDATE agent_traces "
        "SET active_agent = 'rdd_trading_confirmation_agent' "
        "WHERE active_agent = 'fitout_confirm_agent' "
        "AND sub_category = 'FIT_OUT_CONFIRM_TRADING'"
    )


def downgrade() -> None:
    # rdd_handover_agent → rdd_agent
    op.execute(
        "UPDATE chat_sessions "
        "SET active_agent = 'rdd_agent' "
        "WHERE active_agent = 'rdd_handover_agent'"
    )
    op.execute(
        "UPDATE agent_traces "
        "SET active_agent = 'rdd_agent' "
        "WHERE active_agent = 'rdd_handover_agent'"
    )

    # rdd_fitout_start_agent → fitout_confirm_agent
    op.execute(
        "UPDATE chat_sessions "
        "SET active_agent = 'fitout_confirm_agent' "
        "WHERE active_agent = 'rdd_fitout_start_agent'"
    )
    op.execute(
        "UPDATE agent_traces "
        "SET active_agent = 'fitout_confirm_agent' "
        "WHERE active_agent = 'rdd_fitout_start_agent'"
    )

    # rdd_fitout_end_agent → fitout_confirm_agent
    op.execute(
        "UPDATE chat_sessions "
        "SET active_agent = 'fitout_confirm_agent' "
        "WHERE active_agent = 'rdd_fitout_end_agent'"
    )
    op.execute(
        "UPDATE agent_traces "
        "SET active_agent = 'fitout_confirm_agent' "
        "WHERE active_agent = 'rdd_fitout_end_agent'"
    )

    # rdd_trading_confirmation_agent → fitout_confirm_agent
    op.execute(
        "UPDATE chat_sessions "
        "SET active_agent = 'fitout_confirm_agent' "
        "WHERE active_agent = 'rdd_trading_confirmation_agent'"
    )
    op.execute(
        "UPDATE agent_traces "
        "SET active_agent = 'fitout_confirm_agent' "
        "WHERE active_agent = 'rdd_trading_confirmation_agent'"
    )
