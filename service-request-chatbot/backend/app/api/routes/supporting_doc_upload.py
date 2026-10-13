"""POST /api/v2/sales-data/supporting-doc — cache a PDF/image for the next chat turn.

The frontend uploads the file here (multipart) before the chat turn that
triggers ``supporting_doc_upload_node``.  The route stores raw bytes in a
lightweight in-memory cache keyed by session_id so the node can retrieve them
and call ``PUT /files``.

Mirrors ``sales_data_upload.py`` (CSV cache) — no LangGraph, no backend API.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

router = APIRouter()

# session_id → {filename, content_type, raw, size_bytes}
_DOC_CACHE: dict[str, dict[str, Any]] = {}

MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# Must mirror the live cenomi-api PUT /files extension allow-list exactly
# (file_upload/delivery/http/file_upload_handler.go — newFileUploadRequest).
# That handler 400s with "invalid file extension found" for anything outside
# {xlsx, png, doc, pdf, jpg, jpeg, zip} for the sales-data document types
# used here (SD_RE_UPL / SD_SUP_DOC / SD_AUD_DOC — none of the doc-type-
# specific bypasses for archived-drawing / user-guide / HSE video formats
# apply to this flow).  This route used to accept a broader set (docx, xls,
# gif, webp, bmp, tif/tiff) — those files cached here without error, only to
# have the real PUT /files call fail downstream in
# supporting_doc_upload_node, which read as "document upload is failing"
# with no indication of why. Reject them up front instead, with a message
# that says which formats are actually accepted.
# csv is included because a NEED_REUPLOAD request asks for a corrected
# *sales data* file — the same CSV the tenant originally uploaded — and
# cenomi-api's PUT /files allow-list accepts it. Without it the attach
# control offered .csv (per the node's `accept`) and then this endpoint
# rejected the pick with "Unsupported file type".
_ALLOWED_EXTENSIONS = frozenset({
    "pdf", "png", "jpg", "jpeg", "doc", "xlsx", "zip", "csv",
})

_ALLOWED_CONTENT_TYPES = frozenset({
    "application/pdf",
    "application/octet-stream",
    "image/png",
    "image/jpeg",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
    "application/x-zip-compressed",
    # Browsers/OSes report .csv inconsistently (Excel-saved files often come
    # through as vnd.ms-excel, some send no type at all).
    "text/csv",
    "application/csv",
    "text/plain",
    "application/vnd.ms-excel",
})


def store_uploaded_doc(
    session_id: str,
    filename: str,
    content_type: str,
    raw: bytes,
) -> None:
    _DOC_CACHE[session_id] = {
        "filename": filename,
        "content_type": content_type,
        "raw": raw,
        "size_bytes": len(raw),
    }


def get_uploaded_doc(session_id: str) -> dict[str, Any] | None:
    return _DOC_CACHE.get(session_id)


def pop_uploaded_doc(session_id: str) -> dict[str, Any] | None:
    return _DOC_CACHE.pop(session_id, None)


@router.post("/sales-data/supporting-doc")
async def upload_supporting_doc(
    file: UploadFile = File(...),
    session_id: str | None = None,
):
    """Accept a PDF/image/document for a NEED_* tenant response.

    Returns
    -------
    {
      "session_id": "<cache key — send as corrected_fields.doc_session_id>",
      "filename": "<original filename>",
      "content_type": "<mime>",
      "size_bytes": <int>
    }
    """
    filename = file.filename or "document.pdf"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    content_type = (file.content_type or "application/octet-stream").lower()

    if ext not in _ALLOWED_EXTENSIONS and content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. Please upload one of: PDF, PNG, "
                "JPG/JPEG, DOC, XLSX, CSV, or ZIP."
            ),
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(raw) > MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {MAX_SIZE_BYTES // (1024 * 1024)} MB.",
        )

    cache_key = session_id or str(uuid.uuid4())
    store_uploaded_doc(cache_key, filename, content_type, raw)

    log.info(
        "supporting_doc_upload.received",
        session_id=cache_key,
        filename=filename,
        size_bytes=len(raw),
    )

    return JSONResponse(
        content={
            "session_id": cache_key,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": len(raw),
        }
    )
