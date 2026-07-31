"""Build upstream API payloads for Handover Service Request workflows.

Public functions
----------------
- ``build_create_handover_payload``       — CREATE_SR (Mall Manager POST, Path A)
- ``build_create_handed_over_payload``    — CREATE_HANDED_OVER_SR (Mall Manager POST, Path B)
- ``build_fm_review_payload``             — FM_REVIEW save-progress (PATCH IN_PROCESS)
- ``build_fm_approve_payload``            — FM_REVIEW approval (PATCH APPROVED)
- ``build_rdd_report_payload``            — RDD_REVIEW submit report (POST REPORT_SUBMITTED)

Design notes
------------
* Pure deterministic data mapping — no LLM calls, no I/O.
* Required keys are validated before the payload dict is returned; any
  missing key raises ``ValueError`` with a descriptive message so callers
  can surface the error before attempting a network call.
* CREATE inspection datetimes: chatbot extracts date-only ISO; the builder
  normalises to UTC ISO ``startDate``/``endDate`` plus portal LT display
  strings ``startDateLT``/``endDateLT`` (``DD/MM/YYYY h:mm AM/PM``) so the
  classic DateTimePicker can hydrate after create.
* RDD date values from collected_data are in ISO format (YYYY-MM-DD); they
  are normalised to DD/MM/YYYY per the Postman collection shape.
* FM dates are also written as DD/MM/YYYY for portal form hydration.
* Both ``comment`` and ``comments`` are written so the form textarea binds.
* Builders merge from the latest ``create_payload`` snapshot (refreshed via
  platform GET before build) so PATCH/POST replace does not wipe fields.
* RDD report dates are mirrored at payload root **and** in ``document_status_map``.
* RDD final approve prefers the last ``rdd_payload`` so APPROVE preserves
  guideLineLink, docs, and dates.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Portal DateTimePicker + manual CREATE use Asia/Riyadh wall time for LT.
_PORTAL_TZ = ZoneInfo("Asia/Riyadh")
_DEFAULT_INSPECTION_HOUR = 9
_LT_DISPLAY_RE = re.compile(
    r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s+(?:AM|PM)$",
    re.IGNORECASE,
)

# ── Required data keys ────────────────────────────────────────────────────────

# Every key in this set must be present (non-None, non-"", non-[]) in the
# ``data`` dict passed to ``build_create_handover_payload``.
# Optional fields such as "comments" and "notes" are intentionally excluded:
# they may be empty strings when the user declines to provide them, and the
# upstream API accepts an empty string for those fields.
_REQUIRED_DATA_KEYS: frozenset[str] = frozenset(
    {
        "mall",
        "brand",
        "lease_code",
        "title",
        "endDate",
        "startDate",
        "description",
        "inspection_done_by",
        "lease_brand_mall",
        "unit_codes",
        "contracted_area",
        "city",
        "brand_id",
        "tenant_profile_id",
        "contract_id",
        "property_id",
        "lease_id",
    }
)

# ── Internal helpers ───────────────────────────────────────────────────────────


def _validate_required_keys(data: dict[str, Any]) -> None:
    """Raise ``ValueError`` when any required key is absent or empty.

    Absence means: the key is missing from *data*, or its value is ``None``,
    ``""``, or ``[]``.  Integer ``0`` and boolean ``False`` are valid values.
    """
    missing: list[str] = [
        key
        for key in sorted(_REQUIRED_DATA_KEYS)
        if (lambda v: v is None or v == "" or v == [])(data.get(key))
    ]

    if missing:
        raise ValueError(
            f"build_create_handover_payload: missing required data keys: {missing}"
        )


def _format_lt_display(dt_local: datetime) -> str:
    """Format local wall time as portal DateTimePicker LT (``DD/MM/YYYY h:mm AM/PM``)."""
    hour12 = dt_local.hour % 12 or 12
    ampm = "AM" if dt_local.hour < 12 else "PM"
    return (
        f"{dt_local.day:02d}/{dt_local.month:02d}/{dt_local.year} "
        f"{hour12}:{dt_local.minute:02d} {ampm}"
    )


def _parse_lt_display(value: str) -> datetime | None:
    """Parse portal LT display into Asia/Riyadh-aware datetime, or None."""
    text = (value or "").strip()
    if not _LT_DISPLAY_RE.match(text):
        return None
    try:
        date_part, time_part, ampm = text.split()
        day_s, month_s, year_s = date_part.split("/")
        hour_s, minute_s = time_part.split(":")
        hour = int(hour_s)
        minute = int(minute_s)
        ampm_u = ampm.upper()
        if ampm_u == "PM" and hour < 12:
            hour += 12
        elif ampm_u == "AM" and hour == 12:
            hour = 0
        return datetime(
            int(year_s),
            int(month_s),
            int(day_s),
            hour,
            minute,
            0,
            tzinfo=_PORTAL_TZ,
        )
    except (TypeError, ValueError):
        return None


def _parse_hhmm(value: str) -> tuple[int, int] | None:
    """Parse HH:MM (24-hour) or H:MM into (hour, minute), or None on failure."""
    text = (value or "").strip()
    if not text:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def _parse_inspection_datetime(
    value: str, inspection_time: str | None = None
) -> datetime | None:
    """Parse date-only ISO or full ISO into Asia/Riyadh-aware datetime.

    When *inspection_time* is provided (HH:MM from ``collected_data.inspectionTime``),
    it overrides the time component so the user's explicit inspection time is
    preserved.  Falls back to ``_DEFAULT_INSPECTION_HOUR`` when absent.
    """
    text = (value or "").strip()
    if not text:
        return None

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            day = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            return None
        parsed_time = _parse_hhmm(inspection_time or "")
        if parsed_time:
            hour, minute = parsed_time
        else:
            hour, minute = _DEFAULT_INSPECTION_HOUR, 0
        return datetime.combine(day, time(hour, minute, 0), tzinfo=_PORTAL_TZ)

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_PORTAL_TZ)
    local_dt = parsed.astimezone(_PORTAL_TZ)
    # Override time when inspectionTime is explicitly provided
    parsed_time = _parse_hhmm(inspection_time or "")
    if parsed_time:
        hour, minute = parsed_time
        local_dt = local_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local_dt


def _normalize_inspection_datetime(
    iso_or_date: Any,
    existing_lt: Any = None,
    inspection_time: str | None = None,
) -> tuple[str, str]:
    """Return ``(utc_iso, lt_display)`` for CREATE payload inspection dates.

    Chatbot extraction stores date-only ``YYYY-MM-DD``.  The portal CREATE
    contract and DateTimePicker expect UTC ISO plus ``DD/MM/YYYY h:mm AM/PM``.

    When *inspection_time* (HH:MM) is provided it overrides the time component;
    an existing LT display string is only used when no inspection_time is given.
    """
    lt_raw = "" if existing_lt is None else str(existing_lt).strip()
    iso_raw = "" if iso_or_date is None else str(iso_or_date).strip()

    local_dt = _parse_lt_display(lt_raw) if (lt_raw and not inspection_time) else None
    if local_dt is None:
        local_dt = _parse_inspection_datetime(iso_raw, inspection_time)
    if local_dt is None:
        return iso_raw, lt_raw

    utc = local_dt.astimezone(timezone.utc)
    utc_iso = utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
    lt_display = _format_lt_display(local_dt)
    return utc_iso, lt_display


# ── Public API ────────────────────────────────────────────────────────────────


def build_create_handover_payload(
    data: dict[str, Any],
    documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the CREATE_SR submission payload from validated ``collected_data``.

    Parameters
    ----------
    data:
        The validated ``collected_data`` dict from the graph state.  All keys
        listed in ``_REQUIRED_DATA_KEYS`` must be present and non-empty.
    documents:
        Optional uploaded documents (draft / graph state). Their ``document_id``
        values are included in ``payload.documents_ids``.

    Returns
    -------
    dict
        The complete payload ready for the Handover Service Request API.
        ``service_request_id`` is set to ``""`` for new requests (the backend
        assigns an ID on creation).

    Raises
    ------
    ValueError
        If any required key is absent or empty in *data*.
    """
    _validate_required_keys(data)

    # inspectionTime (HH:MM) from collected_data.
    # When explicitly provided, it overrides any stored LT display string.
    # When absent and no LT display exists either, fall back to current wall time
    # so the payload always carries a meaningful time (not just midnight).
    inspection_time: str | None = data.get("inspectionTime") or None
    if not inspection_time and not data.get("startDateLT") and not data.get("endDateLT"):
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        inspection_time = _dt.now(_ZI("Asia/Riyadh")).strftime("%H:%M")

    start_iso, start_lt = _normalize_inspection_datetime(
        data["startDate"], data.get("startDateLT"), inspection_time=inspection_time
    )
    end_iso, end_lt = _normalize_inspection_datetime(
        data["endDate"], data.get("endDateLT"), inspection_time=inspection_time
    )

    doc_ids: list[str] = []
    for doc in documents or []:
        doc_id = doc.get("document_id")
        if doc_id and str(doc_id) not in doc_ids:
            doc_ids.append(str(doc_id))

    payload: dict[str, Any] = {
        "payload": {
            "mall": data["mall"],
            "brand": data["brand"],
            "lease": data["lease_code"],
            "notes": data.get("notes", ""),
            "title": data["title"],
            "endDate": end_iso,
            "comments": data.get("comments", ""),
            "startDate": start_iso,
            "attachments": "",
            "description": data["description"],
            "documents_ids": doc_ids,
            "guideLineLink": "",
            "inspectionDoneBy": data["inspection_done_by"],
            "lease_brand_mall": data["lease_brand_mall"],
            "inspection_done_by": data["inspection_done_by"],
            "already_handovered": "initiate_handover",
            "document_status_map": [],
            "unit_readiness_date": "",
            "expected_handover_date": "",
            "company_name": str(data["tenant_profile_id"]),
            "tenant_contact": "",
            "user_action": None,
            "unit_codes": data["unit_codes"],
            "contracted_area": data["contracted_area"],
            "city": data["city"],
            "brand_id": data["brand_id"],
            "tenant_profile_id": data["tenant_profile_id"],
            "contract_id": data["contract_id"],
            "property_id": data["property_id"],
            "startDateLT": start_lt,
            "endDateLT": end_lt,
        },
        "title": data["title"],
        "tenant_profile_id": data["tenant_profile_id"],
        "property_id": data["property_id"],
        "service_category": "FIT_OUT_AND_HANDOVER",
        "sub_category": "HANDOVER",
        "lease_code": data["lease_code"],
        "lease_id": data["lease_id"],
        "service_request_id": "",
    }

    logger.debug(
        "build_create_handover_payload: payload built for lease_code=%s tenant_profile_id=%s docs=%d",
        data["lease_code"],
        data["tenant_profile_id"],
        len(doc_ids),
    )

    return payload


# ── Path B — Already Trading (HANDED_OVER) payload builder ───────────────────


_REQUIRED_HANDED_OVER_KEYS: frozenset[str] = frozenset(
    {
        "mall",
        "brand",
        "lease_code",
        "lease_id",
        "brand_id",
        "tenant_profile_id",
        "property_id",
        "unit_codes",
        "contracted_area",
        "city",
        "actual_shop_handover_date",
        "actual_fitout_start_date",
        "actual_fitout_end_date",
        "actual_trading_date",
    }
)


def _validate_handed_over_keys(data: dict[str, Any]) -> None:
    """Raise ``ValueError`` when any required HANDED_OVER key is absent or empty."""
    missing: list[str] = [
        key
        for key in sorted(_REQUIRED_HANDED_OVER_KEYS)
        if (lambda v: v is None or v == "" or v == [])(data.get(key))
    ]
    if missing:
        raise ValueError(
            f"build_create_handed_over_payload: missing required data keys: {missing}"
        )


def build_create_handed_over_payload(
    data: dict[str, Any],
    documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the CREATE_HANDED_OVER_SR submission payload (Path B — Already Trading).

    This is the single-step Mall Manager flow for units that are already trading.
    The platform auto-approves the SR immediately on creation.

    Parameters
    ----------
    data:
        Validated ``collected_data`` dict.  All keys in
        ``_REQUIRED_HANDED_OVER_KEYS`` must be present and non-empty.
    documents:
        Optional uploaded documents (``SR_HANDED_OVER_OTHER`` type).
        Their ``document_id`` values are included in ``payload.documents_ids``.

    Returns
    -------
    dict
        Complete payload ready for the Handover Service Request API with
        ``sub_category: HANDED_OVER``.

    Raises
    ------
    ValueError
        If any required key is absent or empty in *data*.
    """
    _validate_handed_over_keys(data)

    doc_ids: list[str] = []
    for doc in documents or []:
        doc_id = doc.get("document_id")
        if doc_id and str(doc_id) not in doc_ids:
            doc_ids.append(str(doc_id))

    lease_code: str = data["lease_code"]
    title: str = data.get("title") or f"Already Trading - {lease_code}"

    payload: dict[str, Any] = {
        "payload": {
            "mall": data["mall"],
            "brand": data["brand"],
            "lease": lease_code,
            "title": title,
            "comments": data.get("comments", ""),
            "attachments": "",
            "documents_ids": doc_ids,
            "already_handovered": "already_trading",
            "document_status_map": [],
            "actual_shop_handover_date": data["actual_shop_handover_date"],
            "actual_fitout_start_date": data["actual_fitout_start_date"],
            "actual_fitout_end_date": data["actual_fitout_end_date"],
            "actual_trading_date": data["actual_trading_date"],
            "company_name": str(data["tenant_profile_id"]),
            "tenant_contact": "",
            "user_action": None,
            "unit_codes": data["unit_codes"],
            "contracted_area": data["contracted_area"],
            "city": data["city"],
            "brand_id": data["brand_id"],
            "tenant_profile_id": data["tenant_profile_id"],
            "property_id": data["property_id"],
            "lease_id": data["lease_id"],
        },
        "title": title,
        "tenant_profile_id": data["tenant_profile_id"],
        "property_id": data["property_id"],
        "service_category": "FIT_OUT_AND_HANDOVER",
        "sub_category": "HANDED_OVER",
        "lease_code": lease_code,
        "lease_id": data["lease_id"],
        "service_request_id": "",
    }

    logger.debug(
        "build_create_handed_over_payload: built for lease_code=%s tenant_profile_id=%s docs=%d",
        lease_code,
        data["tenant_profile_id"],
        len(doc_ids),
    )

    return payload


# ── FM / RDD payload builders ─────────────────────────────────────────────────


def _to_ddmmyyyy(iso_date_str: str) -> str:
    """Convert ``"YYYY-MM-DD"`` to ``"DD/MM/YYYY"``.

    Returns the input unchanged when it cannot be parsed (best-effort).
    """
    if not iso_date_str:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(iso_date_str).strip(), fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return str(iso_date_str)


def _platform_date(value: Any) -> str:
    """Normalise a date for platform form fields (DD/MM/YYYY)."""
    if value in (None, ""):
        return ""
    return _to_ddmmyyyy(str(value))


def _iso_date(value: Any) -> str:
    """Normalise a date to YYYY-MM-DD ISO format.

    Used for fields that the platform writes directly to lease records
    (``unit_readiness_date``, ``expected_handover_date``) where the contract
    sample uses ISO format rather than the portal's DD/MM/YYYY display format.
    """
    if value in (None, ""):
        return ""
    s = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _apply_comments(target: dict[str, Any], comments: str) -> None:
    """Write both ``comment`` and ``comments`` so portal form fields hydrate."""
    text = comments or ""
    target["comment"] = text
    target["comments"] = text


def _strip_meta_from_platform_payload(platform_payload: dict[str, Any]) -> dict[str, Any]:
    """Copy platform GET payload without meta keys that are not form fields."""
    skip = {"workflow_version"}
    return {k: v for k, v in platform_payload.items() if k not in skip}


def snapshot_create_payload_from_platform(
    backend_refs: dict[str, Any],
    platform_payload: dict[str, Any] | None,
    *,
    sr_id: str | None = None,
) -> dict[str, Any]:
    """Refresh ``backend_refs["create_payload"]`` from a platform GET payload.

    Platform PATCH/POST **replaces** the stored payload, so chatbot builders must
    always start from the latest platform snapshot — not a stale CREATE-time copy.
    """
    if not isinstance(platform_payload, dict) or not platform_payload:
        return backend_refs

    cleaned = _strip_meta_from_platform_payload(platform_payload)
    existing = dict(backend_refs.get("create_payload") or {})
    resolved_sr = (
        sr_id
        or backend_refs.get("sr_id")
        or existing.get("service_request_id")
        or ""
    )
    backend_refs["create_payload"] = {
        "payload": cleaned,
        "title": cleaned.get("title") or existing.get("title") or "",
        "tenant_profile_id": (
            cleaned.get("tenant_profile_id")
            or existing.get("tenant_profile_id")
        ),
        "property_id": cleaned.get("property_id") or existing.get("property_id"),
        "service_category": "FIT_OUT_AND_HANDOVER",
        "sub_category": "HANDOVER",
        "lease_code": (
            cleaned.get("lease")
            or cleaned.get("lease_code")
            or existing.get("lease_code")
            or ""
        ),
        "lease_id": cleaned.get("lease_id") or existing.get("lease_id"),
        "service_request_id": resolved_sr,
    }
    return backend_refs


def _inner_base_from_refs(
    backend_refs: dict[str, Any],
    data: dict[str, Any] | None = None,
    *,
    prefer_rdd_payload: bool = False,
) -> dict[str, Any]:
    """Resolve the richest available inner payload as a merge base."""
    create_payload: dict[str, Any] = backend_refs.get("create_payload") or {}
    inner: dict[str, Any] = dict(create_payload.get("payload") or {})

    if prefer_rdd_payload:
        rdd_envelope = backend_refs.get("rdd_payload") or {}
        rdd_inner = rdd_envelope.get("payload")
        if isinstance(rdd_inner, dict) and rdd_inner:
            # REPORT_SUBMITTED body is newer than CREATE; overlay it.
            inner = {**inner, **dict(rdd_inner)}

    if not inner and data:
        inner = _seed_fm_inner_from_collected(data)
    return inner


def _fm_document_status_entry(doc_id: str) -> dict[str, Any]:
    """Build a single document_status_map entry for an FM document."""
    return {
        "id": doc_id,
        "document_status": "",
        "handover_date": "",
        "actual_handover_date": "",
        "fitout_start_date": "",
        "fitout_end_date": "",
        "trading_date": "",
    }


def _rdd_document_status_entry(
    doc_id: str,
    document_status: str = "APPROVED",
    actual_handover_date: str = "",
    fitout_start_date: str = "",
    fitout_end_date: str = "",
    trading_date: str = "",
) -> dict[str, Any]:
    """Build a document_status_map entry for the RDD handover report.

    Parameters
    ----------
    document_status:
        The RDD Project Manager's document review decision.
        Accepted values: ``"APPROVED"``, ``"APPROVED_WITH_CHANGES"``,
        or ``"DISAPPROVED"`` (Rejected Fitout report).
        Defaults to ``"APPROVED"`` when not yet collected.
    """
    return {
        "id": doc_id,
        "document_status": document_status,
        "handover_date": "",
        "actual_handover_date": _platform_date(actual_handover_date),
        "fitout_start_date": _platform_date(fitout_start_date),
        "fitout_end_date": _platform_date(fitout_end_date),
        "trading_date": _platform_date(trading_date),
    }


def _seed_fm_inner_from_collected(data: dict[str, Any]) -> dict[str, Any]:
    """Fallback inner payload when create_payload is missing (open-existing SR)."""
    lease_code = data.get("lease_code") or data.get("lease") or ""
    seeded: dict[str, Any] = {}
    for key in (
        "mall",
        "brand",
        "title",
        "description",
        "startDate",
        "endDate",
        "notes",
        "comments",
        "unit_codes",
        "contracted_area",
        "city",
        "brand_id",
        "tenant_profile_id",
        "contract_id",
        "property_id",
        "lease_id",
        "inspection_done_by",
        "inspectionDoneBy",
        "lease_brand_mall",
        "company_name",
        "guideLineLink",
        "unit_readiness_date",
        "expected_handover_date",
        "actual_handover_date",
        "fitout_start_date",
        "fitout_end_date",
        "trading_date",
    ):
        if data.get(key) not in (None, ""):
            seeded[key] = data[key]
    if lease_code:
        seeded["lease"] = lease_code
        seeded.setdefault("lease_code", lease_code)
    return seeded


def build_fm_review_payload(
    data: dict[str, Any],
    backend_refs: dict[str, Any],
) -> dict[str, Any]:
    """Build the PATCH payload for FM_REVIEW save-progress (status=IN_PROCESS).

    Parameters
    ----------
    data:
        ``collected_data`` from graph state; must include ``unit_readiness_date``
        and ``expected_handover_date``.
    backend_refs:
        ``backend_refs`` from graph state; must include ``sr_id``,
        ``create_payload``, ``uploaded_documents`` (list of document UUIDs),
        and ``sr_operations``.

    Returns
    -------
    dict
        Full PATCH body ready for ``PlatformAPIClient.patch_service_request``.
    """
    sr_id: str = backend_refs.get("sr_id", "")
    create_payload: dict[str, Any] = backend_refs.get("create_payload") or {}
    inner = _inner_base_from_refs(backend_refs, data)

    uploaded_docs: list[str] = list(backend_refs.get("uploaded_documents") or [])
    # Preserve any documents_ids already on the platform payload that the session
    # may not have re-listed (open-existing / partial hydrate).
    for existing_id in inner.get("documents_ids") or []:
        if existing_id and existing_id not in uploaded_docs:
            uploaded_docs.append(existing_id)

    sr_operations: list[dict[str, Any]] = backend_refs.get("sr_operations") or []
    ref_no: str = backend_refs.get("ref_no", "")

    document_status_map = [_fm_document_status_entry(doc_id) for doc_id in uploaded_docs]

    unit_ready = data.get("unit_readiness_date") or inner.get("unit_readiness_date") or ""
    expected_ho = (
        data.get("expected_handover_date") or inner.get("expected_handover_date") or ""
    )
    comments = data.get("comments") or inner.get("comments") or inner.get("comment") or ""

    fm_inner: dict[str, Any] = {
        **inner,
        "unit_readiness_date": _iso_date(unit_ready),
        "expected_handover_date": _iso_date(expected_ho),
        "documents_ids": uploaded_docs,
        "document_status_map": document_status_map,
        "ref_no": ref_no or inner.get("ref_no", ""),
        "sr_id": sr_id,
        "current_sr_status": "IN_PROCESS",
        "document_saved": True,
        "sr_operations": sr_operations or inner.get("sr_operations") or [],
        "user_action": None,
    }
    _apply_comments(fm_inner, str(comments))

    payload: dict[str, Any] = {
        "payload": fm_inner,
        "title": inner.get("title", data.get("title", "")),
        "tenant_profile_id": inner.get("tenant_profile_id", data.get("tenant_profile_id")),
        "property_id": inner.get("property_id", data.get("property_id")),
        "service_category": "FIT_OUT_AND_HANDOVER",
        "sub_category": "HANDOVER",
        "lease_code": inner.get("lease") or inner.get("lease_code") or data.get("lease_code", ""),
        "lease_id": create_payload.get("lease_id") or inner.get("lease_id") or data.get("lease_id"),
        "service_request_id": sr_id,
        "status": "IN_PROCESS",
    }

    logger.debug(
        "build_fm_review_payload: built for sr_id=%s uploaded_docs=%d",
        sr_id,
        len(uploaded_docs),
    )
    return payload


def build_fm_approve_payload(
    data: dict[str, Any],
    backend_refs: dict[str, Any],
    comment: str = "",
) -> dict[str, Any]:
    """Build the PATCH payload for FM_REVIEW approval (status=APPROVED).

    Same shape as save-progress but with top-level ``status="APPROVED"`` and an
    optional ``comment``. Inner ``current_sr_status`` stays ``IN_PROCESS``
    (pre-action state). Top-level ``lease_code``/``lease_id`` are omitted per
    the Postman collection shape for approval.
    """
    base = build_fm_review_payload(data, backend_refs)

    sr_id: str = backend_refs.get("sr_id", "")
    inner = dict(base.get("payload") or {})
    # Keep pre-action status; top-level status carries APPROVED.
    inner["current_sr_status"] = "IN_PROCESS"
    comment_text = comment or data.get("comments", "") or inner.get("comments", "") or ""
    _apply_comments(inner, str(comment_text))

    return {
        "payload": inner,
        "title": base.get("title", ""),
        "tenant_profile_id": base.get("tenant_profile_id"),
        "property_id": base.get("property_id"),
        "sub_category": "HANDOVER",
        "status": "APPROVED",
        "comment": comment_text,
        "user_action": None,
    }


def build_rdd_report_payload(
    data: dict[str, Any],
    backend_refs: dict[str, Any],
) -> dict[str, Any]:
    """Build the POST payload for RDD_REVIEW submit-report (status=REPORT_SUBMITTED).

    Parameters
    ----------
    data:
        ``collected_data`` from graph state; must include the four RDD date
        fields (``actual_handover_date``, ``fitout_start_date``,
        ``fitout_end_date``, ``trading_date``) and ``guideLineLink``.
    backend_refs:
        ``backend_refs`` from graph state; must include ``sr_id``,
        ``create_payload``, ``uploaded_documents`` (FM doc IDs), and
        ``rdd_document_id`` (the RDD report doc UUID).

    Returns
    -------
    dict
        Full POST body ready for ``PlatformAPIClient.submit_report``.
    """
    sr_id: str = backend_refs.get("sr_id", "")
    create_payload: dict[str, Any] = backend_refs.get("create_payload") or {}
    inner = _inner_base_from_refs(backend_refs, data)

    fm_doc_ids: list[str] = list(backend_refs.get("uploaded_documents") or [])
    for existing_id in inner.get("documents_ids") or []:
        if existing_id and existing_id not in fm_doc_ids:
            # Keep FM docs from platform; RDD report id added separately below.
            fm_doc_ids.append(existing_id)

    rdd_doc_id: str | None = backend_refs.get("rdd_document_id")
    rejected_ids: list[str] = [
        d
        for d in list(backend_refs.get("rdd_rejected_document_ids") or [])
        if d
    ]

    all_doc_ids = list(fm_doc_ids)
    if rdd_doc_id and rdd_doc_id not in all_doc_ids:
        all_doc_ids.append(rdd_doc_id)
    for rejected_id in rejected_ids:
        if rejected_id not in all_doc_ids:
            all_doc_ids.append(rejected_id)

    actual = data.get("actual_handover_date") or inner.get("actual_handover_date") or ""
    fit_start = data.get("fitout_start_date") or inner.get("fitout_start_date") or ""
    fit_end = data.get("fitout_end_date") or inner.get("fitout_end_date") or ""
    trading = data.get("trading_date") or inner.get("trading_date") or ""

    # FM map entries: keep ids that are not the Meeting / Rejected reports.
    rdd_special_ids = {d for d in [rdd_doc_id, *rejected_ids] if d}
    fm_only_ids = [d for d in all_doc_ids if d not in rdd_special_ids]
    fm_status_map = [_fm_document_status_entry(doc_id) for doc_id in fm_only_ids]
    rdd_status_entry = (
        _rdd_document_status_entry(
            doc_id=rdd_doc_id,
            document_status=data.get("document_status", "APPROVED"),
            actual_handover_date=str(actual),
            fitout_start_date=str(fit_start),
            fitout_end_date=str(fit_end),
            trading_date=str(trading),
        )
        if rdd_doc_id
        else None
    )
    rejected_status_entries = [
        _rdd_document_status_entry(
            doc_id=rejected_id,
            document_status="DISAPPROVED",
        )
        for rejected_id in rejected_ids
    ]
    document_status_map = (
        fm_status_map
        + ([rdd_status_entry] if rdd_status_entry else [])
        + rejected_status_entries
    )

    comments = data.get("comments") or inner.get("comments") or inner.get("comment") or ""
    guide = data.get("guideLineLink") or inner.get("guideLineLink") or ""

    sr_operations_val: list[dict[str, Any]] = (
        backend_refs.get("sr_operations")
        or inner.get("sr_operations")
        or []
    )

    rdd_inner: dict[str, Any] = {
        **inner,
        "guideLineLink": guide,
        "documents_ids": all_doc_ids,
        "document_status_map": document_status_map,
        # FM dates preserved (normalised) so a REPORT_SUBMITTED replace does not wipe them.
        "unit_readiness_date": _platform_date(
            data.get("unit_readiness_date") or inner.get("unit_readiness_date") or ""
        ),
        "expected_handover_date": _platform_date(
            data.get("expected_handover_date") or inner.get("expected_handover_date") or ""
        ),
        # Mirror RDD dates at payload root so portal form date fields hydrate
        # (in addition to document_status_map entries used by Postman/contract).
        "actual_handover_date": _platform_date(actual),
        "fitout_start_date": _platform_date(fit_start),
        "fitout_end_date": _platform_date(fit_end),
        "trading_date": _platform_date(trading),
        "document_status": data.get("document_status")
        or inner.get("document_status")
        or "APPROVED",
        "current_sr_status": "IN_PROCESS",
        "sr_operations": sr_operations_val,
        "sr_id": sr_id,
        "user_action": None,
    }
    _apply_comments(rdd_inner, str(comments))

    payload: dict[str, Any] = {
        "payload": rdd_inner,
        "title": inner.get("title", data.get("title", "")),
        "tenant_profile_id": inner.get("tenant_profile_id", data.get("tenant_profile_id")),
        "property_id": inner.get("property_id", data.get("property_id")),
        "service_category": "FIT_OUT_AND_HANDOVER",
        "sub_category": "HANDOVER",
        "lease_code": inner.get("lease", data.get("lease_code", "")),
        "lease_id": create_payload.get("lease_id", data.get("lease_id")),
        "service_request_id": sr_id,
        "status": "REPORT_SUBMITTED",
    }

    logger.debug(
        "build_rdd_report_payload: built for sr_id=%s fm_docs=%d rdd_doc=%s",
        sr_id,
        len(fm_only_ids),
        rdd_doc_id,
    )
    return payload


def build_rdd_approve_payload(
    backend_refs: dict[str, Any],
    comment: str = "",
) -> dict[str, Any]:
    """Build the PATCH payload for RDD final approval (status=APPROVED after REPORT_SUBMITTED).

    Starts from the richest available snapshot (last ``rdd_payload`` / platform
    ``create_payload``) so APPROVE does not wipe dates, docs, or guideLineLink.
    """
    sr_id: str = backend_refs.get("sr_id", "")
    create_payload: dict[str, Any] = backend_refs.get("create_payload") or {}
    inner = _inner_base_from_refs(backend_refs, prefer_rdd_payload=True)

    tenant_profile_id = (
        backend_refs.get("tenant_profile_id")
        or inner.get("tenant_profile_id")
        or create_payload.get("tenant_profile_id")
    )
    property_id = (
        backend_refs.get("property_id")
        or inner.get("property_id")
        or create_payload.get("property_id")
    )

    comment_text = (
        comment
        or inner.get("comments")
        or inner.get("comment")
        or ""
    )

    approved_inner: dict[str, Any] = {
        **inner,
        "sr_id": sr_id,
        "current_sr_status": "REPORT_SUBMITTED",
        "user_action": None,
    }
    # Keep dates in platform form format if present.
    for date_key in (
        "unit_readiness_date",
        "expected_handover_date",
        "actual_handover_date",
        "fitout_start_date",
        "fitout_end_date",
        "trading_date",
    ):
        if approved_inner.get(date_key) not in (None, ""):
            approved_inner[date_key] = _platform_date(approved_inner[date_key])

    # Enrich each document_status_map entry with unit_readiness_date and
    # expected_handover_date so the platform receives complete per-doc records.
    unit_readiness = approved_inner.get("unit_readiness_date", "")
    expected_ho = approved_inner.get("expected_handover_date", "")
    for entry in approved_inner.get("document_status_map") or []:
        if isinstance(entry, dict):
            entry.setdefault("unit_readiness_date", unit_readiness)
            entry.setdefault("expected_handover_date", expected_ho)

    _apply_comments(approved_inner, str(comment_text))

    payload: dict[str, Any] = {
        "payload": approved_inner,
        "title": inner.get("title", "") or create_payload.get("title", ""),
        "tenant_profile_id": tenant_profile_id,
        "property_id": property_id,
        "sub_category": "HANDOVER",
        "status": "APPROVED",
        "comment": comment_text,
    }

    logger.debug("build_rdd_approve_payload: built for sr_id=%s", sr_id)
    return payload


# ── PayloadBuilderService class (DI / backward-compatible wrapper) ────────────


class PayloadBuilderService:
    """Thin class wrapper around the module-level payload builder functions.

    Use the module-level functions directly where possible.  This class
    exists for dependency-injection patterns and backward compatibility.
    """

    def build(self, draft: dict[str, object]) -> dict[str, object]:
        """Backward-compatible passthrough (does not validate or transform)."""
        return dict(draft)

    def build_create_handover_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        """Delegate to the module-level ``build_create_handover_payload``."""
        return build_create_handover_payload(data)

    def build_create_handed_over_payload(
        self,
        data: dict[str, Any],
        documents: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Delegate to the module-level ``build_create_handed_over_payload``."""
        return build_create_handed_over_payload(data, documents)

    def build_fm_review_payload(
        self, data: dict[str, Any], backend_refs: dict[str, Any]
    ) -> dict[str, Any]:
        return build_fm_review_payload(data, backend_refs)

    def build_fm_approve_payload(
        self,
        data: dict[str, Any],
        backend_refs: dict[str, Any],
        comment: str = "",
    ) -> dict[str, Any]:
        return build_fm_approve_payload(data, backend_refs, comment)

    def build_rdd_report_payload(
        self, data: dict[str, Any], backend_refs: dict[str, Any]
    ) -> dict[str, Any]:
        return build_rdd_report_payload(data, backend_refs)

    def build_rdd_approve_payload(
        self, backend_refs: dict[str, Any], comment: str = ""
    ) -> dict[str, Any]:
        return build_rdd_approve_payload(backend_refs, comment)
