"""CSV Field Correction Node.

Reached when the user provides missing field values (typically sales_amt)
verbally AFTER a Layer 1 CSV validation failure.

The RDD agent collects missing fields one-by-one through the
`missing_field_node → field_extraction → merge_state` cycle.  The Sales
Data agent replicates that pattern for the CSV correction case:

  csv_validation fails (missing sales_amt for row N)
      ↓
  stores incomplete rows in backend_refs["csv_incomplete_rows"]
      ↓  (user speaks: "row 1 is 1000 Riyal, row 2 is 20000")
  csv_field_correction_node extracts per-row amounts via LLM
      ↓  (all rows now have sales_amt)
  layer2 → confirmation table   (no re-upload needed)
      OR
      ↓  (some rows still missing)
  response asking for the remaining rows  (WAITING_FOR_USER)

The LLM extraction is intentionally simple: it looks for the most natural
patterns users write ("row 1 is 1000", "<lease code>: 85000").
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.llm.gateway import get_default_gateway as get_llm_gateway
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

# ── LLM prompt for per-row amount extraction ──────────────────────────────

_CORRECTION_SYSTEM_PROMPT = """
You are a field extraction assistant for a sales data correction workflow.

The user has already uploaded a CSV file that failed validation because some
rows were missing a sales_amt value.  The user is now providing those values
verbally.  Your job is to extract a mapping of lease_code → sales_amt from
the user's message.

Context provided to you will include:
  - "rows_needing_correction": list of {row_number, lease_code} objects
  - "user_message": what the user just said

Rules:
- Match amounts to lease codes using any of: lease code name, row number (1-indexed),
  ordinal ("first row", "second"), or positional ("last one").
  Lease codes look like LC-1234 or T0028604 — copy the code from the
  rows_needing_correction context, not from these examples.
- sales_amt must be a positive number.  Strip currency symbols and commas.
- If the user provides ONE amount and there is only ONE row needing correction,
  assign it to that row.
- If the user provides amounts in order without labels and there are N rows
  needing correction, assign them positionally (first amount → first missing row).
- Never invent values.  If you cannot determine the mapping, return an empty corrections list.

Output format (JSON only — no markdown fences):
{
  "corrections": [
    {"lease_code": "LC-1234", "sales_amt": 1000.0}
  ],
  "summary": "one sentence"
}
"""


def _build_extraction_user_msg(
    rows_needing_correction: list[dict],
    user_message: str,
) -> str:
    rows_desc = json.dumps(
        [{"row_number": i + 1, "lease_code": r["lease_code"]} for i, r in enumerate(rows_needing_correction)],
        ensure_ascii=False,
    )
    return (
        f"rows_needing_correction: {rows_desc}\n"
        f"user_message: {user_message}"
    )


@trace_node("csv_field_correction", "AGENT")
async def csv_field_correction_node(state: ServiceRequestState) -> dict[str, Any]:
    """Collect missing CSV row amounts from user verbal input and re-run validation."""
    user_message: str = state.get("user_message") or ""
    backend_refs: dict = dict(state.get("backend_refs") or {})

    # Retrieve all rows (complete + incomplete) stored during Layer 1
    all_rows: list[dict] = backend_refs.get("csv_rows_all") or []
    if not all_rows:
        # Fallback: re-read from csv cache if available
        session_id = state.get("session_id") or ""
        try:
            from app.api.routes.sales_data_upload import get_uploaded_csv
            cached = get_uploaded_csv(session_id)
            if cached:
                all_rows = cached["rows"]
        except Exception:
            pass

    if not all_rows:
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "UPLOAD_INTENT",
            "response_message": (
                "I couldn't find your CSV rows to correct. "
                "Please re-upload the file."
            ),
        }

    # Identify rows still missing sales_amt
    rows_needing_correction = [
        r for r in all_rows
        if not r.get("sales_amt") or float(str(r.get("sales_amt", 0) or 0).replace(",", "") or 0) <= 0
    ]

    if not rows_needing_correction:
        # All rows are complete — proceed to Layer 2
        backend_refs["csv_rows"] = all_rows
        return {
            "backend_refs": backend_refs,
            "workflow_stage": "CSV_LAYER2_VALIDATION",
            "status": "IN_PROGRESS",
        }

    if not user_message.strip():
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "Please provide the missing sales amounts so I can update the rows for you."
            ),
        }

    # ── LLM extraction ────────────────────────────────────────────────────
    corrections: list[dict] = []
    try:
        gateway = get_llm_gateway()
        extraction_msg = _build_extraction_user_msg(rows_needing_correction, user_message)
        messages = [
            {"role": "system", "content": _CORRECTION_SYSTEM_PROMPT},
            {"role": "user", "content": extraction_msg},
        ]
        response = await gateway.generate(messages=messages, temperature=0.0)
        raw = response.content.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        corrections = parsed.get("corrections", [])
    except Exception:
        log.warning("csv_field_correction.llm_failed", exc_info=True)

    # Apply corrections to the rows
    correction_map: dict[str, float] = {}
    for c in corrections:
        lc = (c.get("lease_code") or "").strip().upper()
        try:
            amt = float(str(c.get("sales_amt", 0)).replace(",", ""))
            if amt > 0:
                correction_map[lc] = amt
        except (ValueError, TypeError):
            pass

    # Patch rows in-place
    for row in all_rows:
        lc = str(row.get("lease_code", "")).strip().upper()
        if lc in correction_map:
            row["sales_amt"] = correction_map[lc]

    backend_refs["csv_rows_all"] = all_rows

    # Re-check how many are still missing
    still_missing = [
        r for r in all_rows
        if not r.get("sales_amt") or float(str(r.get("sales_amt", 0) or 0).replace(",", "") or 0) <= 0
    ]

    log.info(
        "csv_field_correction.result",
        corrections_applied=len(correction_map),
        still_missing=len(still_missing),
        total_rows=len(all_rows),
    )

    if still_missing:
        missing_list = ", ".join(f"**{r['lease_code']}**" for r in still_missing)
        return {
            "backend_refs": backend_refs,
            "status": "WAITING_FOR_USER",
            "workflow_stage": "CSV_CORRECTION",
            "response_message": (
                f"Updated {len(correction_map)} row(s). "
                f"Still need the sales amount for: {missing_list}. "
                "Please provide those amounts and I'll submit everything together."
            ),
        }

    # All rows corrected — proceed to Layer 2
    backend_refs["csv_rows"] = all_rows
    return {
        "backend_refs": backend_refs,
        "workflow_stage": "CSV_LAYER2_VALIDATION",
        "status": "IN_PROGRESS",
    }
