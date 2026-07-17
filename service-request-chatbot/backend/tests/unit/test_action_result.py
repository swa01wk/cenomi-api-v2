"""Unit tests — chat state action_result mapping."""

from __future__ import annotations


def _action_result_from_graph_status(graph_status: str | None) -> str | None:
    """Mirror orchestration mapping for SUBMITTED/COMPLETED/FAILED."""
    if graph_status in ("SUBMITTED", "COMPLETED"):
        return "success"
    if graph_status == "FAILED":
        return "failed"
    return None


def test_action_result_success_on_submitted() -> None:
    assert _action_result_from_graph_status("SUBMITTED") == "success"


def test_action_result_success_on_completed() -> None:
    assert _action_result_from_graph_status("COMPLETED") == "success"


def test_action_result_failed() -> None:
    assert _action_result_from_graph_status("FAILED") == "failed"


def test_action_result_null_for_waiting() -> None:
    assert _action_result_from_graph_status("WAITING_FOR_USER") is None
    assert _action_result_from_graph_status("IN_PROGRESS") is None
    assert _action_result_from_graph_status("READY_TO_SUBMIT") is None
    assert _action_result_from_graph_status(None) is None
