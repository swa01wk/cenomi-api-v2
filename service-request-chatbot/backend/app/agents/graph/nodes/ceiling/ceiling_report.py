"""Draft inspection report helpers shared by the ceiling create and review nodes.

The RDD Project Manager (level 1) uploads the draft report and marks it
Approved or Approved with changes — while reviewing an existing request, or,
when RDD PM raises the request, during create so the platform can auto-approve
level 1 straight away.
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.ceiling_schema import (
    DOC_STATUS_APPROVED,
    DOC_STATUS_APPROVED_WITH_CHANGES,
    OTHER_DOC_TYPE,
    draft_report_doc_type,
    sub_category_label,
)
from app.agents.services.review_decorations_service import _uploaded_doc_row

# Status chips for the draft report.
STATUS_ACTIONS: dict[str, str] = {
    "ceiling_status_approved": DOC_STATUS_APPROVED,
    "ceiling_status_approved_with_changes": DOC_STATUS_APPROVED_WITH_CHANGES,
}

WITH_CHANGES_RE = re.compile(r"\bwith (the )?changes\b", re.IGNORECASE)
APPROVED_RE = re.compile(r"\bapprov(e|ed|al)\b", re.IGNORECASE)


def status_suggestions() -> dict[str, Any]:
    """Approved / Approved with changes chips, as a ``response_decorations`` entry."""
    options = [
        {"label": label, "value": label, "action": action}
        for label, action in (
            ("Approved", "ceiling_status_approved"),
            ("Approved with changes", "ceiling_status_approved_with_changes"),
        )
    ]
    return {"suggestions": {"kind": "labeled_options", "options": options}}


def status_from_turn(state: ServiceRequestState) -> str | None:
    """The draft report status this turn gave, from a chip or typed text."""
    action = str(state.get("action_override") or "")
    if action in STATUS_ACTIONS:
        return STATUS_ACTIONS[action]
    message = str(state.get("user_message") or "")
    if message and APPROVED_RE.search(message):
        return (
            DOC_STATUS_APPROVED_WITH_CHANGES
            if WITH_CHANGES_RE.search(message)
            else DOC_STATUS_APPROVED
        )
    return None


def document_requirement(state: ServiceRequestState, sub_category: str) -> dict[str, Any]:
    """Upload card for level 1, matching the form's visible types.

    The form shows the draft as "Open Ceiling Inspection Report" (no "(Draft)")
    and "Inspection Other". The rejected-report type stays a platform document
    for a disapproval; it is not offered on this card, so a save cannot file
    the next attachment as that type by default.
    """
    documents = list(state.get("documents") or [])
    draft = draft_report_doc_type(sub_category) or ""
    label = sub_category_label(sub_category)
    labels = {
        draft: f"{label} Report",
        OTHER_DOC_TYPE: "Inspection Other",
    }
    uploaded = {
        str(d.get("document_type_id") or "")
        for d in documents
        if isinstance(d, dict) and d.get("document_id")
    }
    rows = [
        {
            "key": doc_type,
            "label": labels[doc_type],
            "required": doc_type == draft,
            "status": "uploaded" if doc_type in uploaded else "missing",
            **_uploaded_doc_row(documents, doc_type),
        }
        for doc_type in (draft, OTHER_DOC_TYPE)
        if doc_type
    ]
    return {
        "documents": rows,
        "uploadContext": {
            "session_id": str(state.get("session_id") or ""),
            "sr_id": str((state.get("backend_refs") or {}).get("sr_id") or ""),
            "allowedDocumentTypes": [doc_type for doc_type in (draft, OTHER_DOC_TYPE) if doc_type],
        },
    }
