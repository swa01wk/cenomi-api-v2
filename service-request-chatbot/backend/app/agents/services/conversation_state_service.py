"""Hydrate / persist conversation aggregates.

Responsible for mapping between the DB ``ServiceRequestDraft`` table and the
flat state dict that LangGraph nodes read/write.  No LLM calls, no routing
logic belong here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.services.deleted_documents import (
    deleted_document_ids,
    without_deleted,
)
from app.db.repositories.service_request_draft_repo import ServiceRequestDraftRepository

log = structlog.get_logger(__name__)

# A review prompt waiting on the user's next reply (comment, duration…). The
# graph keeps no state between turns, so it rides in collected_data like
# ``_confirmation_status``.
_PENDING_KEY = "_wp_pending"
PENDING_REVIEW_REFS: tuple[str, ...] = (
    "wp_pending_action",
    "wp_pending_stage",
    "wp_pending_sr_id",
    "wp_extension_duration",
)
# The requester's unsaved form edits on a form-edit stage (see
# ``sr_status_sync_node._keep_form_edits``).
_FORM_EDITS_KEY = "_form_edits"


class StateLoadError(RuntimeError):
    """Raised when an existing draft could not be read.

    Deliberately distinct from "no draft exists" so callers never mistake a
    read failure for a fresh conversation and persist over live data.
    """

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Could not load draft state for session {session_id}")
        self.session_id = session_id


class ConversationStateService:
    """Load and persist draft state for a service-request conversation session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._draft_repo = ServiceRequestDraftRepository(session)

    # Sales Data working data that must survive between turns (the CSV the
    # tenant is correcting, the month they asked for, the admin's review
    # list). backend_refs has no DB column, so without this every follow-up
    # turn ("t0112235 is 5000", "reupload") started with the rows gone.
    # One-shot triggers (csv_session_id, doc_session_id) are deliberately NOT
    # persisted — carried over they would re-run the upload on every message.
    _SALES_REFS_KEY = "_sales_refs"
    _SALES_PERSISTED_REFS = (
        "csv_rows",
        "csv_rows_all",
        "csv_filename",
        "asked_period",
        "pending_items",
        "period_mismatch",
        "admin_review_rows",
        "action_required_rows",
        "submission_source",
    )

    async def load(self, session_id: UUID) -> dict[str, Any]:
        """Return the latest draft fields for *session_id*, or ``{}`` if none exists.

        Workflow metadata embedded in ``collected_data`` by ``save_checkpoint``
        (``_confirmation_status``, ``_selected_lease``, ``_active_field``) is
        extracted into top-level state keys so graph nodes see a clean interface.
        """
        try:
            draft = await self._draft_repo.get_by_session(session_id)
            if draft is None:
                return {}

            raw_collected: dict[str, Any] = dict(draft.collected_data or {})

            # Extract embedded workflow metadata back into top-level state keys.
            confirmation_status = raw_collected.pop("_confirmation_status", None)
            selected_lease = raw_collected.pop("_selected_lease", None)
            active_field = raw_collected.pop("_active_field", None)
            sales_refs = raw_collected.pop(self._SALES_REFS_KEY, None)
            pending_review = raw_collected.pop(_PENDING_KEY, None)
            form_edits = raw_collected.pop(_FORM_EDITS_KEY, None)

            result: dict[str, Any] = {
                "service_category": draft.service_category,
                "sub_category": draft.sub_category,
                "workflow_stage": draft.workflow_stage,
                "collected_data": raw_collected,
                "missing_fields": draft.missing_fields or [],
                "documents": draft.documents or [],
            }
            if confirmation_status is not None:
                result["confirmation_status"] = confirmation_status
            if selected_lease is not None:
                result["selected_lease"] = selected_lease
            if active_field is not None:
                result["active_field"] = active_field

            # Restore submitted SR identity into backend_refs so that
            # sr_status_sync routing and the preview node can access the
            # platform-assigned SR ID across turns without re-querying the DB.
            backend_refs: dict[str, Any] = {}
            if draft.sr_id:
                backend_refs["sr_id"] = draft.sr_id
                if draft.service_request_status:
                    backend_refs["service_request_status"] = draft.service_request_status
            if isinstance(pending_review, dict) and pending_review.get("wp_pending_action"):
                backend_refs.update(
                    {k: pending_review[k] for k in PENDING_REVIEW_REFS if k in pending_review}
                )
            if isinstance(form_edits, dict) and form_edits:
                backend_refs["form_edits"] = form_edits
            if backend_refs:
                result["backend_refs"] = backend_refs
            if isinstance(sales_refs, dict) and sales_refs:
                result["backend_refs"] = {**(result.get("backend_refs") or {}), **sales_refs}

            return result
        except Exception:
            # Do NOT degrade to {}. An empty result is indistinguishable from
            # "this session has no draft yet", so the turn would run as a brand
            # new conversation and ``save_checkpoint`` would then overwrite the
            # real draft with near-empty collected_data — destroying the lease
            # resolution and every answer the user has given. Observed live:
            # four concurrent requests reduced a full draft to a single field.
            # Failing the turn is recoverable (the user retries); silently
            # wiping their request is not.
            log.exception("conversation_state_service.load.failed", session_id=str(session_id))
            raise StateLoadError(str(session_id)) from None

    async def save_checkpoint(self, session_id: UUID, state: dict[str, Any]) -> None:
        """Upsert draft state after a completed graph turn.

        Creates a new draft row when the graph has identified a service
        category; skips persistence when the intent is not yet resolved.

        ``confirmation_status``, ``selected_lease``, and ``active_field`` are not
        DB columns, so they are piggy-backed into ``collected_data`` under reserved
        ``_``-prefixed keys.  ``load`` strips them back out into top-level state
        keys so nodes never see the raw storage.
        """
        try:
            service_category = state.get("service_category")
            sub_category = state.get("sub_category") or ""
            workflow_stage = state.get("workflow_stage") or "CREATE_SR"
            ready_to_submit = state.get("status") in ("READY_TO_SUBMIT", "SUBMITTED")

            # Build the collected_data to persist, embedding workflow metadata
            # that has no dedicated DB column.
            raw_collected: dict[str, Any] = dict(state.get("collected_data") or {})
            confirmation_status = state.get("confirmation_status")
            if confirmation_status is not None:
                raw_collected["_confirmation_status"] = confirmation_status
            else:
                raw_collected.pop("_confirmation_status", None)

            selected_lease = state.get("selected_lease")
            if selected_lease is not None:
                raw_collected["_selected_lease"] = selected_lease
            else:
                raw_collected.pop("_selected_lease", None)

            active_field = state.get("active_field")
            if active_field is not None:
                raw_collected["_active_field"] = active_field
            else:
                raw_collected.pop("_active_field", None)

            refs = state.get("backend_refs") or {}
            sales_refs = (
                {k: refs[k] for k in self._SALES_PERSISTED_REFS if refs.get(k) is not None}
                if service_category == "SALES_DATA" or state.get("active_agent") == "sales_data_agent"
                else {}
            )
            if sales_refs:
                raw_collected[self._SALES_REFS_KEY] = sales_refs
            else:
                raw_collected.pop(self._SALES_REFS_KEY, None)

            if refs.get("wp_pending_action"):
                raw_collected[_PENDING_KEY] = {
                    k: refs[k] for k in PENDING_REVIEW_REFS if refs.get(k) is not None
                }
            else:
                raw_collected.pop(_PENDING_KEY, None)
            if isinstance(refs.get("form_edits"), dict) and refs["form_edits"]:
                raw_collected[_FORM_EDITS_KEY] = dict(refs["form_edits"])
            else:
                raw_collected.pop(_FORM_EDITS_KEY, None)

            draft = await self._draft_repo.get_by_session(session_id)

            if draft is None:
                # FM/RDD review sessions and open-existing-SR turns may not have
                # service_category set (registry_node is skipped on those paths).
                if not service_category:
                    sr_id = _extract_sr_id(state)
                    if workflow_stage in ("FM_REVIEW", "RDD_REVIEW") or sr_id:
                        service_category = "FIT_OUT_AND_HANDOVER"
                        if not sub_category:
                            sub_category = "HANDOVER"
                    else:
                        log.debug(
                            "conversation_state_service.save_checkpoint.skipped_no_category",
                            session_id=str(session_id),
                        )
                        return
                await self._draft_repo.create(
                    session_id=session_id,
                    service_category=service_category,
                    sub_category=sub_category,
                    workflow_stage=workflow_stage,
                    collected_data=raw_collected,
                    missing_fields=state.get("missing_fields") or [],
                    documents=state.get("documents") or [],
                    ready_to_submit=ready_to_submit,
                    sr_id=_extract_sr_id(state),
                )
            else:
                # Merge so a sync that only saw platform FM attachments cannot
                # wipe an RDD report that was uploaded into the draft this session.
                from app.agents.graph.nodes.shared.sr_status_sync_node import (
                    _merge_documents,
                )

                state_docs = state.get("documents")
                if state_docs is None:
                    merged_docs = list(draft.documents or [])
                else:
                    merged_docs = _merge_documents(
                        list(draft.documents or []),
                        list(state_docs or []),
                    )
                # The union above can only add; a document deleted this
                # session must not come back from the older draft copy.
                merged_docs = without_deleted(
                    merged_docs, deleted_document_ids(state.get("backend_refs"))
                )
                updates: dict[str, Any] = {
                    "workflow_stage": workflow_stage,
                    # Use raw_collected unconditionally — an empty dict after a
                    # restart must CLEAR the draft, not fall back to stale data.
                    # The old `if raw_collected else draft.collected_data` guard
                    # treated {} as falsy and silently re-persisted the old
                    # collected_data, causing context leakage into the next request.
                    "collected_data": raw_collected,
                    "missing_fields": state.get("missing_fields") or [],
                    "documents": merged_docs,
                    "ready_to_submit": ready_to_submit,
                }
                if service_category:
                    updates["service_category"] = service_category
                if sub_category:
                    updates["sub_category"] = sub_category
                sr_id = _extract_sr_id(state)
                if sr_id:
                    updates["sr_id"] = sr_id
                elif (state.get("backend_refs") or {}).get("released_sr_id"):
                    # The turn deliberately let go of the SR to start a new
                    # request (new_request_node). An sr_id is otherwise only
                    # ever written, never cleared, so the draft would restore
                    # the released SR on the next turn and pull the new request
                    # back onto it. Clear it only on that explicit signal.
                    updates["sr_id"] = None
                    updates["service_request_status"] = None
                await self._draft_repo.update(draft.id, updates)

            log.debug(
                "conversation_state_service.save_checkpoint.ok",
                session_id=str(session_id),
                workflow_stage=workflow_stage,
            )
        except Exception:
            log.exception(
                "conversation_state_service.save_checkpoint.failed",
                session_id=str(session_id),
            )


def _extract_sr_id(state: dict[str, Any]) -> str | None:
    backend_refs = state.get("backend_refs") or {}
    return backend_refs.get("sr_id") or backend_refs.get("service_request_id")
