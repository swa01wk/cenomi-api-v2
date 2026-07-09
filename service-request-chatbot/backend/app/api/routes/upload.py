"""Document upload route with platform integration.

Guards applied (in order)
--------------------------
1. **Role check** — caller must hold a valid upload permission based on
   ``document_type`` (FM docs require ``CAN_FM_REVIEW_HANDOVER_SR``;
   RDD report requires ``CAN_RDD_REVIEW_HANDOVER_SR``; default requires
   ``CAN_RAISE_HANDOVER_SR``).
2. **Content-type allowlist** — only PDF, JPEG, and PNG are accepted; returns
   415 for anything else.
3. **Document-type validation** — ``document_type`` must be a member of
   ``ALL_DOCUMENT_TYPES`` from ``handover_schema``; returns 422 otherwise.

When ``session_id`` is provided, the route loads the draft to extract
``backend_refs`` (sr_id, lease_id, brand_id, etc.) needed by the platform
PUT /files endpoint.  The returned ``document_id`` is written back to the
draft's ``documents`` list so subsequent graph turns can reference it.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.agents.schemas.handover_schema import (
    ALL_DOCUMENT_TYPES,
    FM_ALLOWED_DOCUMENTS,
    RDD_ALLOWED_DOCUMENTS,
)
from app.agents.services.document_upload_service import DocumentUploadService
from app.agents.services.permission_service import PermissionDeniedError, PermissionService
from app.agents.services.review_decorations_service import payload_to_collected_data
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.core.request_context import reset_current_auth_context, set_current_auth_context
from app.core.security import get_auth_context
from app.db.repositories.service_request_draft_repo import ServiceRequestDraftRepository
from app.db.session import DbSession
from app.types.chat import AuthContext

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Allowed MIME types for uploaded files
# ---------------------------------------------------------------------------

_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
    }
)

# RDD report document types require the APPROVED status flag on upload.
_RDD_DOC_TYPES: frozenset[str] = frozenset(RDD_ALLOWED_DOCUMENTS)
_FM_DOC_TYPES: frozenset[str] = frozenset(FM_ALLOWED_DOCUMENTS)

_permission_service = PermissionService()
_upload_service = DocumentUploadService()


def _pick_upload_permission(document_type: str | None) -> str:
    """Return the action name required to upload this document type."""
    if document_type in _RDD_DOC_TYPES:
        return "UPLOAD_RDD_HANDOVER_REPORT"
    if document_type in _FM_DOC_TYPES:
        return "UPLOAD_FM_HANDOVER_DOCUMENT"
    return "CREATE_HANDOVER_SR"


async def _resolve_lease_id_from_code(
    lease_code: str,
    auth: AuthContext,
) -> dict[str, Any]:
    """Resolve numeric lease_id (and related refs) via GET /leases."""
    from app.agents.services.lease_lookup_service import (
        LeaseLookupQuery,
        get_lease_lookup_service,
    )

    ctx_token = set_current_auth_context(auth)
    try:
        result = await get_lease_lookup_service().lookup(
            LeaseLookupQuery(lease_code=str(lease_code).strip())
        )
    finally:
        reset_current_auth_context(ctx_token)

    if result.error or not result.matches:
        logger.warning(
            "upload_document: lease lookup failed for lease_code=%s — %s",
            lease_code,
            result.error or "no matches",
        )
        return {}

    match = result.matches[0]
    return {
        "lease_id": match.lease_id,
        "brand_id": match.brand_id,
        "property_id": match.property_id,
        "lease_code": match.lease_code,
        "tenant_profile_id": match.tenant_profile_id,
    }


async def _hydrate_backend_refs_from_platform(
    sr_id: str,
    backend: dict[str, Any],
    auth: AuthContext,
) -> dict[str, Any]:
    """Fill lease/brand refs from GET /service-requests/{sr_id} when draft lacks them.

    Platform detail responses often omit top-level ``lease_id`` (it is not on
    ``ServiceRequestDetailResponse``). Nested handover payload stores the PMS
    code as ``lease``. When ``lease_id`` is still missing after GET, resolve it
    via lease lookup using that code.
    """
    if backend.get("lease_id"):
        return backend

    ctx_token = set_current_auth_context(auth)
    try:
        result = await get_service_request_api_service().get_service_request(sr_id)
    finally:
        reset_current_auth_context(ctx_token)

    if not result.success:
        logger.warning(
            "upload_document: platform GET sr_id=%s failed — %s",
            sr_id,
            result.error,
        )
        return backend

    hydrated = payload_to_collected_data(result.payload or {})
    merged = dict(backend)
    for key in (
        "lease_id",
        "brand_id",
        "property_id",
        "lease_code",
        "tenant_profile_id",
        "contract_id",
    ):
        if not merged.get(key) and hydrated.get(key) not in (None, "", 0):
            merged[key] = hydrated[key]

    # Prefer contract_id from the SR payload over GET /leases. Lease lookup only
    # returns leases eligible for *new* handover creation, so an existing SR's
    # lease is filtered out (reason=handover_exists) even though the code is valid.
    if not merged.get("lease_id") and merged.get("contract_id") not in (None, "", 0):
        merged["lease_id"] = merged["contract_id"]
        logger.info(
            "upload_document: using contract_id=%s as lease_id for sr_id=%s",
            merged["contract_id"],
            sr_id,
        )

    if not merged.get("lease_id") and merged.get("lease_code"):
        resolved = await _resolve_lease_id_from_code(str(merged["lease_code"]), auth)
        for key, value in resolved.items():
            if not merged.get(key) and value not in (None, "", 0):
                merged[key] = value

    if not merged.get("lease_id"):
        logger.warning(
            "upload_document: GET sr_id=%s ok but lease_id still missing "
            "(lease_code=%s, contract_id=%s, payload_keys=%s)",
            sr_id,
            merged.get("lease_code"),
            merged.get("contract_id"),
            sorted((result.payload or {}).keys()),
        )
    return merged


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/upload")
async def upload_document(
    db: DbSession,
    file: UploadFile = File(...),
    document_type: str | None = Form(default=None),
    session_id: str | None = Form(default=None),
    sr_id: str | None = Form(default=None),
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """Accept a document file, upload to the platform, and return document info.

    Parameters
    ----------
    file:
        The uploaded file (multipart).
    document_type:
        Document-type identifier (e.g. ``"SR_HANDOVER_CHECKLIST"``).
        Must be a member of ``ALL_DOCUMENT_TYPES``.
    session_id:
        Optional chat session UUID.  When provided the route loads draft
        ``collected_data`` to resolve backend refs (sr_id, lease_id, etc.)
        for the platform upload call.
    sr_id:
        Optional explicit service request ID (overrides draft-derived sr_id).
    auth:
        Resolved from the ``Authorization`` header by ``get_auth_context``.
    """
    # 1. Role check -----------------------------------------------------------
    required_action = _pick_upload_permission(document_type)
    try:
        _permission_service.check(required_action, auth)
    except PermissionDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    # 2. Content-type allowlist -----------------------------------------------
    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported file type '{content_type}'. "
                f"Allowed types: {sorted(_ALLOWED_CONTENT_TYPES)}."
            ),
        )

    # 3. Document-type validation ---------------------------------------------
    if document_type is not None and document_type not in ALL_DOCUMENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown document_type '{document_type}'. "
                f"Valid types: {sorted(ALL_DOCUMENT_TYPES)}."
            ),
        )

    # 4. Load draft backend refs from DB -------------------------------------
    draft_backend: dict[str, Any] = {}
    draft_sr_id: str | None = sr_id
    resolved_session_id: UUID | None = None

    if session_id:
        try:
            resolved_session_id = UUID(session_id)
            draft_repo = ServiceRequestDraftRepository(db)
            draft = await draft_repo.get_by_session(resolved_session_id)
            if draft:
                collected: dict[str, Any] = dict(draft.collected_data or {})
                # Use draft sr_id when not explicitly provided
                if not draft_sr_id and draft.sr_id:
                    draft_sr_id = draft.sr_id
                draft_backend = {
                    "lease_id": collected.get("lease_id", ""),
                    "brand_id": collected.get("brand_id", ""),
                    "property_id": collected.get("property_id", ""),
                    "lease_code": collected.get("lease_code", ""),
                    "tenant_profile_id": collected.get("tenant_profile_id", ""),
                    "existing_documents": list(draft.documents or []),
                }
        except (ValueError, Exception) as exc:
            logger.warning(
                "upload_document: failed to load draft for session_id=%s — %s",
                session_id,
                exc,
            )

    # 5. Platform upload (when sr_id and backend refs are available) ----------
    # In mock mode (no SERVICE_REQUEST_API_BASE_URL) backend refs may be absent
    # for FM/RDD sessions that don't carry the CREATE_SR collected_data.
    # The DocumentUploadService.upload_document already short-circuits to a
    # mock document ID in mock mode, so relaxing the lease_id guard is safe.
    from app.core.config import settings as _settings
    _in_mock_mode = not bool(_settings.service_request_api_base_url)

    if draft_sr_id and not draft_backend.get("lease_id") and not _in_mock_mode:
        draft_backend = await _hydrate_backend_refs_from_platform(
            draft_sr_id, draft_backend, auth
        )

    if draft_sr_id and (draft_backend.get("lease_id") or _in_mock_mode):
        file_bytes = await file.read()
        filename = file.filename or "unknown"

        # RDD report documents must carry document_type_status=APPROVED
        doc_type_status = "APPROVED" if document_type in _RDD_DOC_TYPES else ""

        # Forward the caller's AI session Bearer to PUT /files (agentic route).
        # Without this, the platform client falls back to S2S login.
        ctx_token = set_current_auth_context(auth)
        try:
            upload_result = await _upload_service.upload_document(
                file_bytes=file_bytes,
                filename=filename,
                content_type=content_type,
                document_type_id=document_type or "UNKNOWN",
                sr_id=draft_sr_id,
                lease_id=str(draft_backend.get("lease_id", "")),
                brand_id=str(draft_backend.get("brand_id", "")),
                property_id=str(draft_backend.get("property_id", "")),
                lease_code=str(draft_backend.get("lease_code", "")),
                tenant_profile_id=str(draft_backend.get("tenant_profile_id", "")),
                document_type_status=doc_type_status,
            )
        finally:
            reset_current_auth_context(ctx_token)

        if upload_result.error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"Platform upload failed: {upload_result.error}. "
                    "Please try again or contact support."
                ),
            )

        document_id = upload_result.document_id

        # 6. Write document_id back to the draft documents list --------------
        if resolved_session_id and document_id:
            try:
                draft_repo = ServiceRequestDraftRepository(db)
                draft = await draft_repo.get_by_session(resolved_session_id)
                doc_entry: dict[str, Any] = {
                    "document_id": document_id,
                    "document_type_id": document_type,
                    "filename": file.filename or "unknown",
                    "signed_url": upload_result.signed_url,
                    "file_path": upload_result.file_path,
                }
                if draft:
                    existing_docs: list[dict[str, Any]] = list(draft.documents or [])
                    existing_docs.append(doc_entry)
                    await draft_repo.update(draft.id, {"documents": existing_docs})
                else:
                    # FM/RDD sessions may not have a draft yet (no service_category
                    # set during sr_status_sync entry).  Create a minimal draft so
                    # the document is persisted and visible to subsequent turns.
                    await draft_repo.create(
                        session_id=resolved_session_id,
                        service_category="FIT_OUT_AND_HANDOVER",
                        sub_category="HANDOVER",
                        workflow_stage="FM_REVIEW",
                        collected_data={},
                        missing_fields=[],
                        documents=[doc_entry],
                        ready_to_submit=False,
                        sr_id=draft_sr_id or None,
                    )
                await db.commit()
                logger.info(
                    "upload_document: document_id=%s written to draft session_id=%s",
                    document_id,
                    session_id,
                )
            except Exception as exc:
                logger.warning(
                    "upload_document: failed to write document_id to draft — %s (non-fatal)",
                    exc,
                )

        return {
            "filename": file.filename or "unknown",
            "content_type": content_type,
            "document_type": document_type or "unspecified",
            "document_id": document_id,
            "signed_url": upload_result.signed_url,
            "file_path": upload_result.file_path,
            "status": "uploaded",
        }

    # 7. No sr_id or lease refs — cannot call platform PUT /files.
    logger.warning(
        "upload_document: blocked — no sr_id/lease_id available "
        "(session_id=%s, sr_id=%s, document_type=%s)",
        session_id,
        draft_sr_id,
        document_type,
    )
    if draft_sr_id and not draft_backend.get("lease_id"):
        detail = (
            f"Could not resolve lease details for service request {draft_sr_id}. "
            "Reopen the SR from the inbox list, then try attaching again."
        )
    else:
        detail = (
            "Document upload requires an active Service Request. "
            "Please create the service request first, then upload documents."
        )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=detail,
    )
