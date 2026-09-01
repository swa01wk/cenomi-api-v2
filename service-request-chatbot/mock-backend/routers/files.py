"""Mock file upload + document view.

Live Cenomi:
  PUT /files?file_extension=&document_type_id=&lease_id=&file_name=
  GET /document-view?document_id=

The portal admin ⋮ item “Download Supporting Document” calls document-view
and opens the blob in DocumentViewWrapperSalesData.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

router = APIRouter()


def _store(request: Request) -> dict:
    if not hasattr(request.app.state, "files") or request.app.state.files is None:
        request.app.state.files = {}
    return request.app.state.files


@router.put("/files")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    file_extension: str | None = Query(default=None),
    document_type_id: str | None = Query(default=None),
    lease_id: int | None = Query(default=None),
    file_name: str | None = Query(default=None),
):
    """Accept a file, persist bytes, return document_id (same contract as live PUT /files)."""
    content = await file.read()
    doc_id = f"doc-{uuid.uuid4().hex[:12]}"
    filename = file_name or file.filename or f"{doc_id}.bin"
    content_type = file.content_type or "application/octet-stream"
    _store(request)[doc_id] = {
        "bytes": content,
        "filename": filename,
        "content_type": content_type,
    }
    return {
        "document_id": doc_id,
        "filename": filename,
        "size_bytes": len(content),
        "content_type": content_type,
        "file_extension": file_extension,
        "document_type_id": document_type_id,
        "lease_id": lease_id,
    }


@router.get("/document-view")
async def document_view(
    request: Request,
    document_id: str | None = Query(default=None),
):
    """Same path + query as live GET /document-view?document_id= — returns the file blob."""
    if not document_id:
        raise HTTPException(status_code=400, detail="document_id parameter is required")
    rec = _store(request).get(document_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Document not found")
    filename = rec.get("filename") or f"{document_id}.bin"
    media = rec.get("content_type") or "application/octet-stream"
    return Response(
        content=rec["bytes"],
        media_type=media,
        headers={
            "Content-Disposition": f"attachment; filename={quote(filename)}",
        },
    )
