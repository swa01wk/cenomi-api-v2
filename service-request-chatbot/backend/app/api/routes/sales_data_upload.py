"""POST /api/v2/sales-data/upload — receive a CSV file and store it in the session.

The frontend sends the file here (multipart) before the chat turn that triggers
Layer 1 validation.  The route stores parsed CSV rows in a lightweight in-memory
cache keyed by session_id so the ``csv_validation_node`` can retrieve them.

Design notes
------------
- We deliberately do NOT run LangGraph here: the route only stores the raw
  bytes and parsed rows in the cache.  Validation and routing happen on the
  next chat turn when the user sends a confirmation message.
- The in-memory cache is intentionally simple (dict in module scope).  For
  production replace with Redis or the existing database session store.
"""

from __future__ import annotations

import csv
import logging
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.agents.schemas.sales_data_schema import CSV_MAX_SIZE_BYTES, CSV_REQUIRED_HEADERS
from app.agents.services.sales_data_csv import parse_sales_data_csv

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory file cache  session_id → {"rows": [...], "filename": str, "raw": bytes}
# ---------------------------------------------------------------------------

_FILE_CACHE: dict[str, dict[str, Any]] = {}

MAX_SIZE_BYTES = CSV_MAX_SIZE_BYTES
REQUIRED_HEADERS = set(CSV_REQUIRED_HEADERS)


def store_uploaded_csv(session_id: str, rows: list[dict], filename: str, raw: bytes) -> None:
    _FILE_CACHE[session_id] = {"rows": rows, "filename": filename, "raw": raw}


def pop_uploaded_csv(session_id: str) -> dict[str, Any] | None:
    return _FILE_CACHE.pop(session_id, None)


def get_uploaded_csv(session_id: str) -> dict[str, Any] | None:
    return _FILE_CACHE.get(session_id)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/sales-data/upload")
async def upload_sales_data_csv(
    file: UploadFile = File(...),
    session_id: str | None = None,
):
    """Accept a CSV file upload for sales data submission.

    Returns
    -------
    {
      "session_id": "<id used as cache key>",
      "filename": "<original filename>",
      "row_count": <int>,
      "columns": ["lease_code", ...]
    }

    The session_id returned here must be sent on the next chat turn so the
    ``csv_validation_node`` can retrieve the parsed rows.
    """
    # File type check
    if file.content_type not in ("text/csv", "application/csv", "text/plain", "application/octet-stream"):
        # Allow any content type but validate by parsing
        pass

    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Only .csv files are accepted.",
        )

    raw = await file.read()

    if len(raw) > MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {MAX_SIZE_BYTES // (1024*1024)} MB.",
        )

    # Parse CSV — Cenomi platform template (Lease code, Brand, Property, dates).
    try:
        text = raw.decode("utf-8-sig")  # strip BOM if present
        parsed = parse_sales_data_csv(text)
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid CSV file: {exc}") from exc
    except csv.Error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid CSV file: {exc}") from exc

    if parsed.missing_headers:
        raise HTTPException(
            status_code=400,
            detail=(
                "CSV is missing required columns: "
                + ", ".join(parsed.missing_headers)
                + ". Accepted: lease_code, sales_period_start, sales_period_end, sales_amt "
                "or the Cenomi template (Lease code, Brand, Property, "
                "Start date(DD-MM-YYYY), End date(DD-MM-YYYY), Sales amount). "
                "Brand and Property are optional."
            ),
        )

    rows = parsed.rows
    headers = parsed.headers_found or list(CSV_REQUIRED_HEADERS)

    if not rows:
        raise HTTPException(status_code=400, detail="CSV file is empty — no data rows found.")

    cache_key = session_id or str(uuid.uuid4())
    store_uploaded_csv(cache_key, rows, file.filename or "upload.csv", raw)

    log.info(
        "sales_data_upload.csv_received",
        session_id=cache_key,
        filename=file.filename,
        row_count=len(rows),
    )

    return JSONResponse(
        content={
            "session_id": cache_key,
            "filename": file.filename,
            "row_count": len(rows),
            "columns": list(headers),
        }
    )
