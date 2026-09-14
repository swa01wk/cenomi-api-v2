"""Supporting Document Upload Node.

Resolves which NEED_* row the tenant is responding to, then either:
  - prompts for a PDF/image if no file is cached yet, or
  - calls PUT /files and stores the returned document_id.

Reached for UPLOAD_SUPPORTING_DOCUMENT (NEED_REUPLOAD PDF, supporting
document, or audited sales).
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.sales_data_schema import (
    ASK_UPLOAD_COPY,
    ASK_UPLOAD_LABELS,
    DOC_TYPE_BY_STATUS,
    STATUS_LABELS,
    TENANT_ACTION_STATUSES,
    TENANT_RESPONSE_STATUS,
)
from app.agents.services.permission_service import graph_permission_guard
from app.agents.services.sales_data_api_client import (
    SalesDataApiClient,
    normalize_period_date,
)
from app.api.routes.supporting_doc_upload import get_uploaded_doc, pop_uploaded_doc
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

_LEASE_RE = re.compile(r"\bLC[-_]?\d+\b", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def _normalise_lease(code: str) -> str:
    return code.upper().replace("_", "-")


def _document_id_from_upload(result: Any) -> str | None:
    """Read document_id from a flat payload or live Cenomi ``{ success, data }`` envelope."""
    if not isinstance(result, dict):
        return None
    nested = result.get("data") if isinstance(result.get("data"), dict) else {}
    for candidate in (
        result.get("document_id"),
        result.get("id"),
        nested.get("document_id") if isinstance(nested, dict) else None,
        nested.get("id") if isinstance(nested, dict) else None,
    ):
        if candidate is None:
            continue
        value = str(candidate).strip()
        if value:
            return value
    return None


def _lease_from_text(text: str) -> str | None:
    match = _LEASE_RE.search(text or "")
    return _normalise_lease(match.group(0)) if match else None


def _period_from_text(text: str) -> tuple[str | None, str | None]:
    dates = _ISO_DATE_RE.findall(text or "")
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        return dates[0], None
    return None, None


def _ask_copy(status: str) -> dict[str, str]:
    return ASK_UPLOAD_COPY.get(status) or ASK_UPLOAD_COPY["NEED_REUPLOAD"]


def _requested_need_status(text: str) -> str | None:
    """Infer which Ask Upload type the tenant is responding to."""
    lowered = (text or "").lower()
    if any(k in lowered for k in ("audit", "audited")):
        return "NEED_UPLOAD_AUDITED_SALES"
    if any(k in lowered for k in ("supporting", "extra doc", "extra document")):
        return "NEED_UPLOAD_SUPPORTING_DOCUMENT"
    if any(
        k in lowered
        for k in ("re-upload", "reupload", "need_reupload", "corrected figures")
    ):
        return "NEED_REUPLOAD"
    return None


def _prompt_for_file(row: dict[str, Any]) -> dict[str, Any]:
    lease_code = row.get("lease_code", "")
    start = row.get("sales_period_start", "")
    end = row.get("sales_period_end", "")
    status = row.get("status", "")
    ask_label = ASK_UPLOAD_LABELS.get(status, "document")
    copy = _ask_copy(status)
    comment = (row.get("comment_text") or "").strip()
    target = TENANT_RESPONSE_STATUS.get(status, "RE_UPLOADED")
    amt = row.get("sales_amt")
    amt_str = (
        f" Current amount: **SAR {float(amt):,.2f}**."
        if amt is not None and amt != ""
        else ""
    )

    # Kept deliberately short — status/ask-type/target-status labels are
    # internal jargon the tenant doesn't need up front; the confirm card
    # (after attaching) shows the full detail as an actual table instead of
    # cramming it into this plain-text prompt.
    admin_note = f' Note from the Sales Data team: *"{comment}"*.' if comment else ""
    message = (
        f"Please attach {copy['prompt_verb']} for lease **{lease_code}** "
        f"({start} – {end}).{amt_str}{admin_note}\n\n"
        "A comment is required — attach the file below."
    )
    return {
        "workflow_stage": "AWAITING_DOC_UPLOAD",
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "supporting_doc_prompt",
            "verbatim": True,
            "lease_code": lease_code,
            "lease_id": row.get("lease_id"),
            "sales_period_start": start,
            "sales_period_end": end,
            "current_status": status,
            "target_status": target,
            "ask_label": ask_label,
            "sales_amt": amt,
            "comment_text": comment or None,
            "show_file_upload": True,
            # Kept in sync with supporting_doc_upload.py's _ALLOWED_EXTENSIONS,
            # which mirrors the live cenomi-api PUT /files allow-list — picking
            # a format outside this set (e.g. GIF/WEBP/TIFF, which "image/*"
            # alone would otherwise let through) used to cache fine here and
            # only fail once forwarded to the real backend.
            "accept": ".pdf,.png,.jpg,.jpeg,.doc,.xlsx,.zip",
            "message": message,
        },
    }


def _upload_failure_retry(
    collected: dict[str, Any],
    backend_refs: dict[str, Any],
    doc_session_id: str | None,
    notice: str,
) -> dict[str, Any]:
    """Build the retry response for a failed PUT /files call.

    Reuses ``_prompt_for_file`` so the response always carries
    ``response_ui.show_file_upload = True`` — the exception branches used to
    return a bare ``response_message`` with no ``response_ui`` at all, which
    left any consumer that keys off ``ui.type`` (rather than
    ``workflow_stage``) with no way to know an attachment control should be
    shown, and the LLM response-rewrite node had nothing but the raw
    exception text to work from.

    The cached bytes that just failed are dropped (and ``doc_session_id``
    cleared) so a retry always requires a fresh attach instead of silently
    re-submitting the same bytes the backend just rejected — which would
    otherwise reproduce the identical failure forever with no way out.
    """
    if doc_session_id:
        pop_uploaded_doc(doc_session_id)
    remaining = {
        k: v for k, v in collected.items() if k not in {"document_id", "filename"}
    }
    refs = dict(backend_refs)
    refs.pop("doc_session_id", None)
    prompt = _prompt_for_file(
        {
            "lease_id": remaining.get("lease_id"),
            "lease_code": remaining.get("lease_code"),
            "sales_period_start": remaining.get("sales_period_start"),
            "sales_period_end": remaining.get("sales_period_end"),
            "status": remaining.get("current_status"),
            "comment_text": remaining.get("admin_comment"),
            "sales_amt": remaining.get("sales_amt"),
        }
    )
    message = f"{notice}\n\n{prompt['response_message']}"
    prompt["response_message"] = message
    prompt["response_ui"]["message"] = message
    prompt["collected_data"] = remaining
    prompt["backend_refs"] = refs
    return prompt


def _list_rows_prompt(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lines = [
        "I found these submissions that need a file from you "
        "(sales data, supporting document, or audited sales). "
        "Which lease should I attach the file to?\n",
    ]
    ui_rows = []
    for row in rows:
        lc = row.get("lease_code", "?")
        status = row.get("status", "")
        label = STATUS_LABELS.get(status, status)
        ask_label = ASK_UPLOAD_LABELS.get(status, "")
        period = f"{row.get('sales_period_start', '')} – {row.get('sales_period_end', '')}"
        ask_bit = f" — {ask_label}" if ask_label else ""
        lines.append(f"  • **{lc}** ({period}){ask_bit} — {label}")
        ui_rows.append(row)
    example = next((str(r.get("lease_code") or "") for r in rows if r.get("lease_code")), "")
    if example:
        lines.append(f"\nReply with the lease code (e.g. {example}), then attach the PDF.")
    else:
        lines.append("\nReply with the lease code, then attach the PDF.")
    message = "\n".join(lines)
    return {
        "workflow_stage": "DOC_SELECT",
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "supporting_doc_select",
            "verbatim": True,
            "rows": ui_rows,
            "message": message,
        },
    }


@trace_node("supporting_doc_upload", "AGENT")
async def supporting_doc_upload_node(state: ServiceRequestState) -> dict[str, Any]:
    """Select the target NEED_* row and upload the cached file via PUT /files."""
    collected: dict = dict(state.get("collected_data") or {})
    backend_refs: dict = dict(state.get("backend_refs") or {})
    corrected_fields: dict = dict(state.get("corrected_fields") or {})
    user_message: str = state.get("user_message") or ""

    notification = corrected_fields.get("notification_context") or {}
    if isinstance(notification, dict):
        if notification.get("lease_code") and not collected.get("lease_code"):
            collected["lease_code"] = _normalise_lease(str(notification["lease_code"]))

    lease_from_msg = _lease_from_text(user_message)
    if lease_from_msg:
        collected["lease_code"] = lease_from_msg

    comment_hint = (corrected_fields.get("comment_text") or "").strip()
    if comment_hint:
        collected["comment_text"] = comment_hint

    # ── Resolve target row ─────────────────────────────────────────────────
    if not collected.get("lease_id"):
        token = get_current_bearer_token()
        client = SalesDataApiClient(bearer_token=token)
        try:
            rows = await client.get_sales_data(disable_pagination=True)
        except Exception:
            log.error("supporting_doc_upload.list_failed", exc_info=True)
            message = "⚠️ I couldn't load your sales data right now. Please try again."
            return {
                "status": "WAITING_FOR_USER",
                "response_message": message,
                "response_ui": {"type": "message", "message": message, "verbatim": True},
            }

        # Normalize the raw live-API datetime ("2026-05-01T04:00:00+04:00")
        # down to a bare date here, once, before it enters collected_data —
        # otherwise it flows straight through to the PATCH /sales-data
        # payload later in supporting_doc_submission_node, which
        # cenomi-api rejects (400) since it expects a plain date, and it
        # also renders as ugly raw ISO text anywhere shown to the tenant.
        # Same normalization partition_rows_by_lease_access already applies
        # for the CSV/manual-entry paths — this flow reads GET /sales-data
        # rows directly instead, so it never went through that helper.
        for r in rows:
            if r.get("sales_period_start"):
                r["sales_period_start"] = normalize_period_date(r["sales_period_start"])
            if r.get("sales_period_end"):
                r["sales_period_end"] = normalize_period_date(r["sales_period_end"])

        action_rows = [r for r in rows if r.get("status") in TENANT_ACTION_STATUSES]
        wanted_kind = _requested_need_status(user_message)
        if wanted_kind and not collected.get("lease_code"):
            matching_kind = [r for r in action_rows if r.get("status") == wanted_kind]
            if matching_kind:
                action_rows = matching_kind
        if not action_rows:
            message = (
                "I couldn't find any submissions that need a file from you. "
                "There is no **Need Re-upload**, supporting-document, or audited-sales "
                "request on your leases. Check the Sales Data listing page if you "
                "were expecting one."
            )
            return {
                "workflow_stage": "STALE_NOTIFICATION",
                "status": "WAITING_FOR_USER",
                "response_message": message,
                "response_ui": {
                    "type": "stale_notification",
                    "verbatim": True,
                    "show_listing_link": True,
                    "message": message,
                },
            }

        wanted = collected.get("lease_code")
        if wanted:
            wanted_n = _normalise_lease(str(wanted))
            matches = [
                r
                for r in action_rows
                if _normalise_lease(str(r.get("lease_code", ""))) == wanted_n
            ]
            start, end = _period_from_text(user_message)
            if start:
                period_matches = [
                    r
                    for r in matches
                    if str(r.get("sales_period_start") or "") == start
                    or (
                        not end
                        and str(r.get("sales_period_start") or "").startswith(start[:7])
                    )
                ]
                if end:
                    period_matches = [
                        r
                        for r in matches
                        if str(r.get("sales_period_start") or "") == start
                        and str(r.get("sales_period_end") or "") == end
                    ]
                matches = period_matches or matches
            if not matches:
                listed = _list_rows_prompt(action_rows)
                listed["response_message"] = (
                    f"Lease **{wanted}** is not waiting for a document.\n\n"
                    + listed["response_message"]
                )
                listed["response_ui"]["message"] = listed["response_message"]
                return listed
            action_rows = matches

        if len(action_rows) > 1:
            backend_refs["action_required_rows"] = action_rows
            result = _list_rows_prompt(action_rows)
            result["backend_refs"] = backend_refs
            result["collected_data"] = collected
            return result

        row = action_rows[0]
        collected.update(
            {
                "lease_id": row.get("lease_id"),
                "lease_code": row.get("lease_code"),
                "sales_period_start": row.get("sales_period_start"),
                "sales_period_end": row.get("sales_period_end"),
                "current_status": row.get("status"),
                "sales_amt": row.get("sales_amt"),
                "admin_comment": row.get("comment_text"),
            }
        )

    # ── Retrieve cached file ───────────────────────────────────────────────
    doc_session_id: str | None = (
        corrected_fields.get("doc_session_id")
        or backend_refs.get("doc_session_id")
    )
    cached = get_uploaded_doc(doc_session_id or "") if doc_session_id else None

    if not cached:
        if doc_session_id:
            log.warning(
                "supporting_doc_upload.cache_miss",
                doc_session_id=doc_session_id,
            )
        prompt = _prompt_for_file(
            {
                "lease_id": collected.get("lease_id"),
                "lease_code": collected.get("lease_code"),
                "sales_period_start": collected.get("sales_period_start"),
                "sales_period_end": collected.get("sales_period_end"),
                "status": collected.get("current_status"),
                "comment_text": collected.get("admin_comment"),
                "sales_amt": collected.get("sales_amt"),
            }
        )
        prompt["collected_data"] = collected
        prompt["backend_refs"] = backend_refs
        return prompt

    current_status = collected.get("current_status") or "NEED_REUPLOAD"
    document_type_id = DOC_TYPE_BY_STATUS.get(current_status, "SD_RE_UPL")
    filename = cached["filename"]
    content_type = cached.get("content_type") or "application/octet-stream"
    raw: bytes = cached["raw"]

    denied = graph_permission_guard(state, "SUBMIT_SALES_DATA_SUPPORTING_DOC")
    if denied:
        log.warning("supporting_doc_upload.permission_denied")
        return denied

    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)
    try:
        result = await client.upload_file(
            raw,
            filename,
            content_type,
            document_type_id=document_type_id,
            lease_id=collected.get("lease_id"),
            lease_code=collected.get("lease_code"),
        )
    except Exception as exc:
        log.error(
            "supporting_doc_upload.put_files_failed",
            exc_info=True,
            lease_code=collected.get("lease_code"),
            document_type_id=document_type_id,
        )
        return _upload_failure_retry(
            collected,
            backend_refs,
            doc_session_id,
            f"⚠️ The file upload failed ({exc}). Please attach the file again.",
        )

    document_id = _document_id_from_upload(result)
    if not document_id:
        log.error(
            "supporting_doc_upload.missing_document_id",
            lease_code=collected.get("lease_code"),
            document_type_id=document_type_id,
            put_files_response=result,
        )
        return _upload_failure_retry(
            collected,
            backend_refs,
            doc_session_id,
            "⚠️ The file server did not return a document ID. Please attach the file again.",
        )

    collected["document_id"] = document_id
    collected["filename"] = filename
    collected["document_type_id"] = document_type_id
    collected["target_status"] = TENANT_RESPONSE_STATUS.get(
        current_status, "RE_UPLOADED"
    )
    if doc_session_id:
        backend_refs["doc_session_id"] = doc_session_id
    backend_refs["document_id"] = document_id

    log.info(
        "supporting_doc_upload.put_ok",
        lease_code=collected.get("lease_code"),
        document_id=document_id,
        document_type_id=document_type_id,
    )
    return {
        "collected_data": collected,
        "backend_refs": backend_refs,
        "workflow_stage": "AWAITING_DOC_CONFIRM",
        "confirmation_status": "PENDING",
        "status": "IN_PROGRESS",
    }
