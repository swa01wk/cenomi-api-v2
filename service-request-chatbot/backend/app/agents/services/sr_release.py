"""Letting go of a finished SR when the same session starts a new request.

The session keeps one draft row, and that row outlives the SR it produced: its
``sr_id`` is restored into ``backend_refs`` on every turn and its ``documents``
are unioned with each turn's list on save. A new request started over a
finished one therefore has to say so explicitly, or the save step writes the
old SR straight back — its documents reappear in the new flow's upload card,
and deleting one there deletes the old SR's file on the platform, because it is
the same document id.

``released_sr_id`` in ``backend_refs`` is that signal. ``save_checkpoint``
reads it to clear the draft's ``sr_id`` and to replace (not merge) its
documents; it lives for the one turn that starts the new request.
"""

from __future__ import annotations

from typing import Any

RELEASED_SR_KEY = "released_sr_id"


def released_backend_refs(state: dict[str, Any]) -> dict[str, Any]:
    """``backend_refs`` for a turn that starts a new request over an old one."""
    sr_id = (state.get("backend_refs") or {}).get("sr_id")
    return {RELEASED_SR_KEY: sr_id} if sr_id else {}


def releases_sr(backend_refs: dict[str, Any] | None) -> bool:
    return bool((backend_refs or {}).get(RELEASED_SR_KEY))
