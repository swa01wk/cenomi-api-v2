"""Blind merge of graph ``response_decorations`` into the HTTP ``ui`` payload.

Orchestration-only — must not branch on workflow_stage or business rules.
"""

from __future__ import annotations

from typing import Any


def finalize_response_ui(
    ui: dict[str, Any],
    decorations: dict[str, Any] | None,
    *,
    draft_preview: dict[str, Any] | None,
    response_message: str,
) -> dict[str, Any]:
    """Merge optional decorations into *ui* without interpreting workflow stage."""
    result = dict(ui or {"type": "message"})
    deco = decorations or {}

    if deco.get("suggestions") and not result.get("suggestions"):
        if result.get("type") not in ("confirmation_card",):
            result["suggestions"] = deco["suggestions"]

    doc_req = deco.get("document_requirement")
    # Promote upload card only over generic message UIs — never overwrite
    # field questions (description, dates, etc.).
    if doc_req and result.get("type") in (None, "message", "document_requirement"):
        result = {
            **result,
            "type": "document_requirement",
            "documents": doc_req.get("documents", []),
            "uploadContext": doc_req.get("uploadContext", {}),
            "message": result.get("message") or response_message,
        }

    if deco.get("promote_preview") and draft_preview and result.get("type") == "message":
        promoted = dict(draft_preview)
        promoted.setdefault("message", response_message)
        # Keep stage action chips (e.g. Final Approve) — promote must not drop them.
        if result.get("suggestions") and not promoted.get("suggestions"):
            promoted["suggestions"] = result["suggestions"]
        result = promoted

    if result.get("type") == "message" and not result.get("message"):
        result["message"] = response_message

    return result
