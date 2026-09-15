"""Documents the user deleted after the chat had recorded them.

A deleted document is gone from the platform, but its id lived on in three
places: ``state["documents"]`` (merged only additively), the saved draft's
documents (unioned with state on every save, never trimmed), and the platform's
snapshot of the SR payload, whose ``documents_ids`` still lists the id after
the attachment row is deleted. The payload builders union all three, so the
submit carried the deleted id and the platform refused it with
"Document ID not found".

The form's delete sends the cleared row with the id that went
(``deleted_document_id``). merge_state records it on ``backend_refs`` and every
place that rebuilds a document list filters against it.
"""

from __future__ import annotations

from typing import Any

DELETED_IDS_KEY = "deleted_document_ids"


def deleted_ids_from_corrected(corrected: dict[str, Any] | None) -> set[str]:
    """Ids the cleared rows of a form/chat delete patch say were deleted.

    Only an explicit marker counts. A row that merely reads ``missing`` may be
    the chat's stale view of a type the form uploaded on its own.
    """
    rows = (corrected or {}).get("documents")
    if not isinstance(rows, list):
        return set()
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        marker = str(row.get("deleted_document_id") or "").strip()
        if marker:
            ids.add(marker)
    return ids


def deleted_document_ids(backend_refs: dict[str, Any] | None) -> set[str]:
    return {
        str(d).strip()
        for d in ((backend_refs or {}).get(DELETED_IDS_KEY) or [])
        if str(d).strip()
    }


def record_deleted_documents(backend_refs: dict[str, Any], ids: set[str]) -> None:
    """Remember ``ids`` on ``backend_refs`` (mutates it)."""
    if not ids:
        return
    backend_refs[DELETED_IDS_KEY] = sorted(deleted_document_ids(backend_refs) | ids)


def without_deleted(
    documents: list[dict[str, Any]], deleted: set[str]
) -> list[dict[str, Any]]:
    if not deleted:
        return documents
    return [
        d
        for d in documents
        if not (isinstance(d, dict) and str(d.get("document_id") or "").strip() in deleted)
    ]


def without_deleted_ids(ids: list[str], deleted: set[str]) -> list[str]:
    if not deleted:
        return ids
    return [i for i in ids if str(i or "").strip() not in deleted]
