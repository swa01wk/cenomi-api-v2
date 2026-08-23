"""Mock PUT /files endpoint."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, UploadFile

router = APIRouter()


@router.put("/files")
async def upload_file(file: UploadFile = File(...)):
    """Accept any file upload and return a mock document_id."""
    doc_id = f"doc-{uuid.uuid4().hex[:12]}"
    content = await file.read()
    return {
        "document_id": doc_id,
        "filename": file.filename,
        "size_bytes": len(content),
        "content_type": file.content_type,
    }
