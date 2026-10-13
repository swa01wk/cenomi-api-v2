"""Chat orchestration service.

This service is the single entry point that the HTTP layer calls for every
chat turn.  It owns the turn lifecycle:

  1. Load or create ChatSession
  2. Start observability trace
  3. Audit: turn started
  4. Persist user message
  5. Build initial LangGraph state (inject services, carry forward session fields)
  6. Invoke compiled graph
  7. Finish / fail trace
  8. Persist assistant message
  9. Update ChatSession from graph result
 10. Audit: turn completed
 11. Return ChatTurnResult

Principle: this service delegates graph routing and LLM work to LangGraph.
It never contains business workflow logic — validation, field extraction,
submission are concerns of graph nodes and domain services.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph.help_agent_graph import get_compiled_help_graph
from app.core.request_context import reset_current_auth_context, set_current_auth_context
from app.types.chat import AuthContext

# Tests patch app.services.chat_orchestration_service.get_compiled_graph
get_compiled_graph = get_compiled_help_graph


def _is_on_a_service_request_form(msp_context: dict[str, Any] | None) -> bool:
    """True when the page context names a specific Service Request type.

    The chat widget publishes the SR form's own help labels, so a turn sent
    from a Service Request page already says which SR the user is working on.
    Their reviewer inbox is a different job and does not belong on top of it.
    """
    if not isinstance(msp_context, dict):
        return False
    return bool(str(msp_context.get("help_subcategory") or "").strip())


def trace_dimensions(
    auth_context: AuthContext | None, channel: str | None
) -> dict[str, Any]:
    """Who asked and from where, taken from the caller's JWT claims.

    Anonymous shadow-mode turns carry no claims, so their role and mall
    dimensions stay NULL rather than being recorded as empty lists.
    """
    dims: dict[str, Any] = {"channel": normalize_channel(channel)}
    if auth_context is None or auth_context.subject_id in (
        "anonymous",
        "unauthenticated",
    ):
        return dims
    dims["user_roles"] = sorted(str(r) for r in auth_context.roles)
    dims["mall_names"] = list(auth_context.mall_names)
    dims["property_ids"] = list(auth_context.unique_property_ids)
    dims["is_global_admin"] = bool(auth_context.is_global_admin)
    return dims


def _build_auth_context(role: str | None) -> AuthContext:
    """Build an AuthContext from a role string using the ROLE_PERMISSION_MAP.

    Used by tests and internal utilities to construct a typed AuthContext
    without needing a real JWT. Returns empty-roles AuthContext for unknown
    or None roles.
    """
    from app.agents.services.permission_service import ROLE_PERMISSION_MAP
    roles: frozenset[str] = ROLE_PERMISSION_MAP.get(role or "", frozenset()) if role else frozenset()
    return AuthContext(subject_id="chat_user", tenant_id=None, roles=roles)


from app.agents.services.review_decorations_service import (
    build_inbox_decorations,
    derive_user_role,
)
from app.agents.services.conversation_state_service import ConversationStateService
from app.agents.services.notification_intent import NOTIFICATION_INTENTS
from app.services.response_ui_finalize import finalize_response_ui
from app.core.injection_guard import scan_message
from app.db.models import ChatSession
from app.db.repositories.audit_log_repo import AuditLogRepository
from app.db.repositories.chat_message_repo import ChatMessageRepository
from app.db.repositories.chat_session_repo import ChatSessionRepository
from app.observability.analytics_constants import (
    EVENT_FIELD_CORRECTED,
    EVENT_FLOW_CANCELLED,
    EVENT_SESSION_STARTED,
    FLOW_CANCEL_ACTIONS,
    normalize_channel,
)
from app.observability.analytics_emit import emit_analytics
from app.observability.trace_manager import TraceManager

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Per-session turn serialisation
# ---------------------------------------------------------------------------
# A turn is read-modify-write over the whole draft: load_session reads
# collected_data, the graph mutates it, save_state writes the entire blob back.
# Two turns for one session running concurrently therefore lose one side's
# work — the last writer wins. Verified live: four parallel requests reduced a
# fully populated draft (lease, dates, title, notes, comments) to a single
# field. Users reach this by double-clicking send, a client retry, or two open
# tabs. Turns within one conversation are logically sequential, so serialising
# them costs nothing semantically.
#
# Scope: this lock is per-process. It fully covers a single uvicorn worker
# (how this service runs today). Across multiple workers or replicas a
# Postgres advisory lock keyed on session_id would be required; the
# StateLoadError guard in ConversationStateService is the cross-process
# backstop that prevents a failed read from destroying data.
_SESSION_LOCKS: dict[UUID, asyncio.Lock] = {}
_SESSION_LOCKS_GUARD = asyncio.Lock()


async def _acquire_session_lock(session_id: UUID) -> asyncio.Lock:
    """Return (creating if needed) the lock guarding *session_id*'s turns."""
    async with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            _SESSION_LOCKS[session_id] = lock
        return lock


async def _release_session_lock_if_idle(session_id: UUID) -> None:
    """Drop the lock once no turn holds or awaits it, so the map cannot grow
    without bound over a long-lived process."""
    async with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(session_id)
        if lock is not None and not lock.locked():
            _SESSION_LOCKS.pop(session_id, None)


def resolve_turn_sr_id(
    *,
    session_id: UUID | None,
    sr_id: str | None,
    message: str,
) -> str | None:
    """Drop a stale sr_id when the client starts a new conversation with a message.

    Opening an existing SR uses a new session, empty message, and sr_id.
    Create another resets the FE session then immediately sends a seed
    message; React has not flushed ``activeSrId`` yet so the previous SR
    is still attached. Binding that id loads FM_REVIEW and Mall Managers
    see a permission error plus FM document cards.
    """
    if session_id is None and sr_id and str(message or "").strip():
        log.info("chat.drop_stale_sr_id_on_new_session", sr_id=sr_id)
        return None
    return sr_id


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChatTurnState:
    intent: str | None
    workflow_stage: str | None
    missing_fields: list[str]
    ready_to_submit: bool
    rdd_status: str | None = None
    fm_status: str | None = None
    sr_id: str | None = None
    action_result: str | None = None  # "success" | "failed" | None
    confirmation_status: str | None = None
    # "guided" | "open" — whether the user asked the assistant to drive the
    # form. The client locks or unlocks the form on this; it cannot infer the
    # mode from the active field, which is named in both modes.
    guided_tour: str | None = None


@dataclass(frozen=True, slots=True)
class ChatTurnResult:
    session_id: UUID
    active_agent: str | None
    message: str
    ui: dict[str, Any]
    state: ChatTurnState
    trace_id: UUID | None
    draft_preview: dict[str, Any] | None = None
    faq_sources: list[dict] = field(default_factory=list)
    # Persisted chat_messages.id of the assistant reply — the durable
    # per-message identity clients use to attach feedback.
    assistant_message_id: UUID | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ChatOrchestrationService:
    """Orchestrate a single chat turn end-to-end."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._session_repo = ChatSessionRepository(db)
        self._message_repo = ChatMessageRepository(db)
        self._audit_repo = AuditLogRepository(db)
        self._trace_manager = TraceManager(db)
        self._state_service = ConversationStateService(db)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_turn(
        self,
        *,
        session_id: UUID | None,
        user_id: UUID,
        message: str,
        attachments: list[dict[str, Any]],
        action: str | None = None,
        selected_lease_id: str | None = None,
        corrected_fields: dict[str, Any] | None = None,
        auth_context: AuthContext | None = None,
        sr_id: str | None = None,
        language: str | None = None,
        msp_context: dict[str, Any] | None = None,
        viewing_notification_id: str | None = None,
        viewing_service_request_id: str | None = None,
        viewing_notification: dict[str, Any] | None = None,
        active_notification_id: dict[str, Any] | None = None,
        notification_list_more: dict[str, Any] | None = None,
        channel: str | None = None,
    ) -> ChatTurnResult:
        """Run one turn, serialised against other turns for the same session.

        See ``_SESSION_LOCKS``: a turn is a read-modify-write over the entire
        draft, so overlapping turns silently discard each other's work.
        A brand-new session (no id yet) has nothing to contend with.
        """
        kwargs = dict(
            session_id=session_id,
            user_id=user_id,
            message=message,
            attachments=attachments,
            action=action,
            selected_lease_id=selected_lease_id,
            corrected_fields=corrected_fields,
            auth_context=auth_context,
            sr_id=sr_id,
            language=language,
            msp_context=msp_context,
            viewing_notification_id=viewing_notification_id,
            viewing_service_request_id=viewing_service_request_id,
            viewing_notification=viewing_notification,
            active_notification_id=active_notification_id,
            notification_list_more=notification_list_more,
            channel=channel,
        )
        if session_id is None:
            return await self._process_turn_serialised(**kwargs)

        lock = await _acquire_session_lock(session_id)
        if lock.locked():
            log.info("chat.turn_queued_behind_concurrent_turn",
                     session_id=str(session_id))
        async with lock:
            try:
                result = await self._process_turn_serialised(**kwargs)
                # Commit inside the lock. The repositories only flush(); the
                # commit normally happens at request teardown, which runs after
                # the lock is released — so the next queued turn would read
                # pre-commit state and overwrite this one's work. Holding the
                # lock across the commit is what actually makes the turns
                # sequential rather than merely non-overlapping.
                await self._db.commit()
                return result
            finally:
                await _release_session_lock_if_idle(session_id)

    async def _process_turn_serialised(
        self,
        *,
        session_id: UUID | None,
        user_id: UUID,
        message: str,
        attachments: list[dict[str, Any]],
        action: str | None = None,
        selected_lease_id: str | None = None,
        corrected_fields: dict[str, Any] | None = None,
        auth_context: AuthContext | None = None,
        sr_id: str | None = None,
        language: str | None = None,
        msp_context: dict[str, Any] | None = None,
        viewing_notification_id: str | None = None,
        viewing_service_request_id: str | None = None,
        viewing_notification: dict[str, Any] | None = None,
        active_notification_id: dict[str, Any] | None = None,
        notification_list_more: dict[str, Any] | None = None,
        channel: str | None = None,
    ) -> ChatTurnResult:
        """Execute one complete chat turn and return structured result."""

        # Opening a notification carries an authoritative SR reference — it must
        # never be dropped as a "stale" sr_id, even alongside a seed message.
        notification_ref = active_notification_id or (msp_context or {}).get(
            "active_notification_id"
        )
        if isinstance(notification_ref, str) and notification_ref.strip():
            # Reconciled-spec fallback: a bare string is the SR id.
            notification_ref = {"id": notification_ref.strip()}
        if isinstance(notification_ref, dict) and notification_ref.get("id"):
            # The open ref is authoritative SR context — skip the stale-drop.
            pass
        else:
            notification_ref = None
            sr_id = resolve_turn_sr_id(
                session_id=session_id, sr_id=sr_id, message=message
            )

        # 1. Load or create session ----------------------------------------
        chat_session = await self._load_or_create_session(session_id, user_id)
        session_uuid = chat_session.id
        is_new_session = session_id is None or chat_session.id != session_id

        log.info(
            "chat.turn_started",
            session_id=str(session_uuid),
            user_id=str(user_id),
            is_new_session=is_new_session,
        )

        try:
            await self._session_repo.touch(session_uuid)
        except Exception:
            log.exception("analytics.session_touch.failed", session_id=str(session_uuid))

        if is_new_session:
            await emit_analytics(
                self._db,
                user_id=user_id,
                session_id=session_uuid,
                event_name=EVENT_SESSION_STARTED,
            )
        if corrected_fields:
            await emit_analytics(
                self._db,
                user_id=user_id,
                session_id=session_uuid,
                event_name=EVENT_FIELD_CORRECTED,
                properties={"keys": sorted(str(k) for k in corrected_fields.keys())},
            )
        if action in FLOW_CANCEL_ACTIONS:
            await emit_analytics(
                self._db,
                user_id=user_id,
                session_id=session_uuid,
                event_name=EVENT_FLOW_CANCELLED,
                properties={"workflow_stage": chat_session.workflow_stage},
            )

        # 2. Start trace -------------------------------------------------------
        trace_id = await self._trace_manager.start_trace(
            session_id=session_uuid,
            user_id=user_id,
            input_message=message,
            metadata={"attachments_count": len(attachments)},
            dimensions=trace_dimensions(auth_context, channel),
        )

        # 3. Audit: turn started -----------------------------------------------
        await self._audit_repo.create(
            session_id=session_uuid,
            action="turn.started",
            actor_user_id=user_id,
            metadata={"trace_id": str(trace_id) if trace_id else None},
        )

        # 3b. Load recent conversation history (before persisting current msg) ----
        recent_msgs = await self._message_repo.list_recent_by_session(
            session_uuid, limit=10
        )
        conversation_history = [
            {"role": m.role, "content": m.content} for m in recent_msgs
        ]

        # 3c. Prompt-injection scan -------------------------------------------
        # Run before the user message is persisted so that high-risk content
        # never reaches the graph or the message store.
        scan_result = scan_message(message)

        if scan_result.matched_patterns:
            log.warning(
                "security.suspicious_input",
                session_id=str(session_uuid),
                user_id=str(user_id),
                risk_score=scan_result.risk_score,
                matched_patterns=scan_result.matched_patterns,
                is_high_risk=scan_result.is_high_risk,
            )

        if scan_result.is_high_risk:
            # Audit the attempt — store only scan metadata, not the raw message.
            await self._audit_repo.create(
                session_id=session_uuid,
                action="security.injection_attempt",
                actor_user_id=user_id,
                metadata={
                    "risk_score": scan_result.risk_score,
                    "matched_patterns": scan_result.matched_patterns,
                    "reason": scan_result.reason,
                    "trace_id": str(trace_id) if trace_id else None,
                },
            )
            if trace_id:
                await self._trace_manager.fail_trace(
                    trace_id=trace_id,
                    error_message="Blocked: high-risk prompt injection detected.",
                    workflow_stage_before=chat_session.workflow_stage,
                )
            refusal = (
                "I'm sorry, but I can't process that request. "
                "If you need help with a Handover Service Request, "
                "please describe what you'd like to do."
            )
            return ChatTurnResult(
                session_id=session_uuid,
                active_agent=None,
                message=refusal,
                ui={"type": "message"},
                state=ChatTurnState(
                    intent=None,
                    workflow_stage=None,
                    missing_fields=[],
                    ready_to_submit=False,
                ),
                trace_id=trace_id,
            )

        # 4. Persist user message -----------------------------------------------
        # Capture an explicit Python-side timestamp so that the user message and
        # the assistant reply (persisted after graph invocation) get distinct
        # created_at values.  PostgreSQL NOW() returns the *transaction start*
        # time — both INSERTs in a single transaction would otherwise share the
        # same microsecond, making list_recent_by_session ordering undefined.
        user_msg_time = datetime.now(timezone.utc)
        await self._message_repo.create(
            session_id=session_uuid,
            role="user",
            content=message,
            metadata={
                "attachments": attachments,
                "trace_id": str(trace_id) if trace_id else None,
            },
            created_at=user_msg_time,
        )

        # 5. Build initial graph state -----------------------------------------
        initial_state: dict[str, Any] = {
            "session_id": str(session_uuid),
            "user_id": str(user_id),
            "user_message": message,
            "attachments": attachments,
            "trace_id": str(trace_id) if trace_id else "",
            "conversation_history": conversation_history,
            # Non-serialisable runtime services (dropped by serializers before trace persist)
            "trace_manager": self._trace_manager,
            "conversation_state_service": self._state_service,
            # Carry forward routing state so the graph skips re-classification
            "active_agent": chat_session.active_agent,
            "intent": chat_session.intent,
            "workflow_stage": chat_session.workflow_stage,
            # RBAC — injected from JWT, never from frontend request body
            "auth_context": auth_context,
        }

        # Inject UI-layer overrides so graph nodes can act on explicit button
        # actions and inline card edits without relying solely on text parsing.
        if action is not None:
            initial_state["action_override"] = action
        if corrected_fields:
            initial_state["corrected_fields"] = corrected_fields
        if selected_lease_id:
            initial_state["selected_lease"] = {"id": selected_lease_id}
        # sr_id passed by frontend when FM/DD opens an existing SR
        backend_refs = dict(initial_state.get("backend_refs") or {})
        if auth_context is not None:
            user_role = derive_user_role(auth_context)
            if user_role:
                backend_refs["user_role"] = user_role
            if (
                not sr_id
                and notification_ref is None
                and not chat_session.active_agent
                and not _is_on_a_service_request_form(msp_context)
            ):
                # Only show the Handover reviewer inbox when there is no active
                # workflow.  Fitout/Trading confirmation agents in CREATE_SR stage
                # have no sr_id yet but must not see the Handover SR picker.
                #
                # ``chat_session.active_agent`` is empty on the FIRST turn of a
                # new session, so that check alone let the inbox through exactly
                # when it does the most damage: an RDD Project Manager opening
                # the Fitout Start Confirmation form and asking for one got the
                # correct question plus five pending *handover* SRs underneath
                # it, and tapping one moved the session into that handover —
                # which then asked for the handover report's guideline link.
                # Being on a Service Request form is the answer: that page says
                # what they came to do, and it is not triage their queue.
                inbox_deco = build_inbox_decorations(auth_context)
                if inbox_deco:
                    initial_state["response_decorations"] = inbox_deco
        if sr_id:
            backend_refs["sr_id"] = sr_id
        if notification_ref is not None:
            # OPEN_NOTIFICATION — the user tapped a notification whose payload
            # carries the SR id (platform text_content.id).  The graph's
            # open_notification_node (reconciled-spec routing shim) turns this
            # into backend_refs.sr_id + active_agent and hands off to
            # sr_status_sync.
            initial_state["active_notification_id"] = notification_ref
            initial_state["intent"] = "OPEN_NOTIFICATION"
            log.info(
                "chat.open_notification",
                sr_id=notification_ref.get("id"),
                sub_category=notification_ref.get("sub_category"),
            )
        if backend_refs:
            initial_state["backend_refs"] = backend_refs
        # MSP Platform fields — injected by the MSP adapter layer only;
        # absent (None) on all existing /api/chat/service-request turns.
        if language is not None:
            initial_state["language"] = language
        if msp_context is not None:
            initial_state["msp_context"] = msp_context
        list_more = notification_list_more or (msp_context or {}).get(
            "notification_list_more"
        )
        if isinstance(list_more, dict) and list_more.get("sub_category"):
            # View-more paging — deterministic LIST_NOTIFICATIONS turn.
            initial_state["notification_list_more"] = list_more

        ctx = dict(msp_context or {})
        snapshot = viewing_notification or ctx.get("viewing_notification")
        notif_id = (
            viewing_notification_id
            or ctx.get("viewing_notification_id")
            or (snapshot.get("id") if isinstance(snapshot, dict) else None)
            or (snapshot.get("notification_id") if isinstance(snapshot, dict) else None)
        )
        sr_view_id = (
            viewing_service_request_id
            or ctx.get("viewing_service_request_id")
            or (snapshot.get("related_sr_id") if isinstance(snapshot, dict) else None)
            or (snapshot.get("sr_id") if isinstance(snapshot, dict) else None)
        )
        if notif_id:
            initial_state["viewing_notification_id"] = str(notif_id)
        if sr_view_id:
            initial_state["viewing_service_request_id"] = str(sr_view_id)
        if isinstance(snapshot, dict) and snapshot:
            initial_state["viewing_notification"] = snapshot

        # 6. Invoke graph -------------------------------------------------------
        result_state: dict[str, Any]
        auth_ctx_token = set_current_auth_context(auth_context)
        try:
            graph = get_compiled_help_graph()
            result_state = await graph.ainvoke(initial_state)
        except Exception as exc:
            log.exception(
                "chat.graph_invocation_failed",
                session_id=str(session_uuid),
                trace_id=str(trace_id) if trace_id else None,
            )
            if trace_id:
                await self._trace_manager.fail_trace(
                    trace_id=trace_id,
                    error_message=str(exc),
                    workflow_stage_before=chat_session.workflow_stage,
                )
            await self._audit_repo.create(
                session_id=session_uuid,
                action="turn.failed",
                actor_user_id=user_id,
                metadata={
                    "error": str(exc),
                    "trace_id": str(trace_id) if trace_id else None,
                },
            )
            raise
        finally:
            reset_current_auth_context(auth_ctx_token)

        # 7. Extract response fields from graph result -------------------------
        response_message: str = (
            result_state.get("response_message") or "I'm processing your request."
        )
        response_ui: dict[str, Any] = result_state.get("response_ui") or {"type": "message"}
        active_agent: str | None = result_state.get("active_agent")
        graph_status: str = result_state.get("status") or "IN_PROGRESS"

        # 8. Finish trace -------------------------------------------------------
        answer_status = _classify_answer_status(result_state, graph_status)
        if trace_id:
            await self._trace_manager.finish_trace(
                trace_id=trace_id,
                output_message=response_message,
                final_state=result_state,
                active_agent=result_state.get("active_agent"),
                intent=result_state.get("intent"),
                service_category=result_state.get("service_category"),
                sub_category=result_state.get("sub_category"),
                workflow_stage_before=chat_session.workflow_stage,
                workflow_stage_after=result_state.get("workflow_stage"),
                answer_status=answer_status,
            )

        # 9. Persist assistant reply -------------------------------------------
        faq_sources: list[dict] = result_state.get("faq_sources") or []
        assistant_msg_time = datetime.now(timezone.utc)
        assistant_msg = await self._message_repo.create(
            session_id=session_uuid,
            role="assistant",
            content=response_message,
            metadata={
                "active_agent": active_agent,
                "status": graph_status,
                "trace_id": str(trace_id) if trace_id else None,
                "faq_sources": faq_sources,
            },
            created_at=assistant_msg_time,
        )

        # 10. Update session routing state -------------------------------------
        await self._sync_session(chat_session, result_state, graph_status)

        # 11. Audit: turn completed --------------------------------------------
        await self._audit_repo.create(
            session_id=session_uuid,
            action="turn.completed",
            actor_user_id=user_id,
            after_state={
                "active_agent": active_agent,
                "workflow_stage": result_state.get("workflow_stage"),
                "status": graph_status,
            },
            metadata={"trace_id": str(trace_id) if trace_id else None},
        )

        log.info(
            "chat.turn_completed",
            session_id=str(session_uuid),
            active_agent=active_agent,
            graph_status=graph_status,
            trace_id=str(trace_id) if trace_id else None,
        )

        # 12. Build and return result ------------------------------------------
        missing_fields: list[str] = result_state.get("missing_fields") or []
        ready_to_submit = graph_status in ("READY_TO_SUBMIT", "SUBMITTED")
        final_backend_refs: dict[str, Any] = result_state.get("backend_refs") or {}
        rdd_status: str | None = final_backend_refs.get("rdd_status")
        fm_status: str | None = final_backend_refs.get("fm_status")
        # faq_sources already extracted in step 9 above

        if graph_status in ("SUBMITTED", "COMPLETED"):
            action_result: str | None = "success"
        elif graph_status == "FAILED":
            action_result = "failed"
        else:
            action_result = None

        # Build a preview snapshot from collected_data on every turn.
        # Sent alongside every response so the frontend keeps its SR preview card
        # in sync regardless of the primary response_ui type.
        # When sr_id is present the card is marked isSubmitted=True so the
        # frontend disables the Edit button on an already-submitted SR.
        collected = result_state.get("collected_data") or {}
        sr_id = (result_state.get("backend_refs") or {}).get("sr_id")
        # Fields the validator stripped this turn (e.g. an unparseable date).
        # Surfaced so the FE bridge can wipe them from Formik / draftFields
        # immediately, otherwise the invalid value stays visible in the form
        # even though the AI already rejected it.
        cleared_fields: list[str] = sorted({
            str(e.get("field"))
            for e in (result_state.get("validation_errors") or [])
            if e.get("validation_type") == "date_format" and e.get("field")
        })
        # Handover-only: the "sr_preview_card" shape below (fixed
        # requestType="Handover Service Request" + Handover FIELD_LABELS via
        # _fields_from_collected) is meaningless — and actively misleading —
        # for any other agent. Observed live: a Sales Data Agent turn
        # (active_agent="sales_data_agent", collected_data={lease_code: ...}
        # from the supporting-doc-upload flow) still got a draft_preview
        # card labelled "Handover Service Request", because this block used
        # to build one unconditionally whenever collected_data was non-empty.
        is_handover_agent = result_state.get("active_agent") == "rdd_handover_agent"
        is_work_permit_agent = result_state.get("active_agent") == "work_permit_agent"
        fitout_preview_fields = _fitout_draft_preview_fields(
            result_state.get("active_agent"), collected
        )
        draft_preview: dict[str, Any] | None = None
        if is_handover_agent and (collected or cleared_fields):
            from app.agents.graph.nodes.handover.preview_node import _fields_from_collected
            draft_preview = {
                "type": "sr_preview_card",
                "requestType": "Handover Service Request",
                "srId": sr_id,
                "status": "SUBMITTED" if sr_id else "DRAFT",
                "isSubmitted": bool(sr_id),
                "fields": _fields_from_collected(collected),
            }
            if cleared_fields:
                draft_preview["cleared_fields"] = cleared_fields
        elif is_work_permit_agent and (collected or cleared_fields):
            from app.agents.schemas.work_permit_form_fields import (
                wp_draft_fields_from_collected,
            )

            draft_preview = {
                "type": "sr_preview_card",
                "requestType": "Work Permit Service Request",
                "srId": sr_id,
                "status": "SUBMITTED" if sr_id else "DRAFT",
                "isSubmitted": bool(sr_id),
                "fields": wp_draft_fields_from_collected(collected),
            }
            if cleared_fields:
                draft_preview["cleared_fields"] = cleared_fields
        elif fitout_preview_fields:
            # The fit-out agents never had a draft_preview, and it is the only
            # channel that carries the chat's fields to the browser — the form
            # sync applies what it merges. A lease picked from the chip still
            # filled the form, because the frontend merges that row itself; a
            # lease the user *typed* reached the backend and stopped there, so
            # company / brand / mall stayed blank on all four fit-out forms.
            draft_preview = {
                "type": "sr_preview_card",
                "requestType": "Fitout Service Request",
                "srId": sr_id,
                "status": "SUBMITTED" if sr_id else "DRAFT",
                "isSubmitted": bool(sr_id),
                "fields": fitout_preview_fields,
            }
            if cleared_fields:
                draft_preview["cleared_fields"] = cleared_fields

        # Published on every turn so the form knows whether to lock itself.
        from app.agents.services import guided_tour_service

        guided_tour_mode: str | None = (
            guided_tour_service.mode(collected)
            if guided_tour_service.answered(collected)
            else None
        )

        decorations: dict[str, Any] = dict(result_state.get("response_decorations") or {})
        response_ui = finalize_response_ui(
            response_ui,
            decorations,
            draft_preview=draft_preview,
            response_message=response_message,
        )

        return ChatTurnResult(
            session_id=session_uuid,
            active_agent=active_agent,
            message=response_message,
            ui=response_ui,
            state=ChatTurnState(
                intent=result_state.get("intent"),
                workflow_stage=result_state.get("workflow_stage"),
                missing_fields=missing_fields,
                ready_to_submit=ready_to_submit,
                rdd_status=rdd_status,
                fm_status=fm_status,
                sr_id=sr_id,
                action_result=action_result,
                confirmation_status=result_state.get("confirmation_status"),
                guided_tour=guided_tour_mode,
            ),
            trace_id=trace_id,
            draft_preview=draft_preview,
            faq_sources=faq_sources,
            assistant_message_id=assistant_msg.id,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _load_or_create_session(
        self, session_id: UUID | None, user_id: UUID
    ) -> ChatSession:
        """Return an existing ChatSession or create a fresh one.

        If *session_id* is provided and found in the DB, that session is
        returned.  If it is not found (e.g. client sent a stale UUID), a new
        session is created so the turn succeeds without error.
        """
        if session_id is not None:
            existing = await self._session_repo.get_by_id(session_id)
            if existing is not None:
                log.debug("chat.session_loaded", session_id=str(session_id))
                return existing
            log.warning(
                "chat.session_not_found_creating_new",
                requested_session_id=str(session_id),
                user_id=str(user_id),
            )

        new_session = await self._session_repo.create(user_id=user_id)
        log.info("chat.session_created", session_id=str(new_session.id))
        return new_session

    async def _sync_session(
        self,
        chat_session: ChatSession,
        result_state: dict[str, Any],
        graph_status: str,
    ) -> None:
        """Persist routing fields that changed during this turn."""
        updates: dict[str, Any] = {}

        new_agent = result_state.get("active_agent")
        # Include None so that a restart (which clears active_agent) is
        # persisted immediately.  The old `if new_agent` guard skipped the
        # update when active_agent became None, leaving the DB stale and
        # causing the next turn to start with the wrong agent context.
        if new_agent != chat_session.active_agent:
            updates["active_agent"] = new_agent

        new_intent = result_state.get("intent")
        # Never overwrite a meaningful, established intent with UNKNOWN.
        # UNKNOWN is only a transient "clarification needed" signal — persisting
        # it would cause the next turn to start cold and re-ask the user to
        # re-state their intent even mid-workflow.
        # Intent is cleared legitimately via explicit cancel/restart actions
        # (handover_entry_node writes intent=None), which is handled by the
        # new_agent != chat_session.active_agent guard above flushing that state.
        is_unknown = (new_intent or "").upper() == "UNKNOWN"
        # OPEN_NOTIFICATION is a routing seed, not a workflow intent — the SR
        # workflow it lands in persists its own intents on later turns.
        is_transient = (
            is_unknown
            or (new_intent or "") in NOTIFICATION_INTENTS
            or (new_intent or "") == "OPEN_NOTIFICATION"
        )
        if new_intent != chat_session.intent and not is_transient:
            updates["intent"] = new_intent

        new_stage = result_state.get("workflow_stage")
        if new_stage and new_stage != chat_session.workflow_stage:
            updates["workflow_stage"] = new_stage

        if graph_status in ("COMPLETED", "SUBMITTED"):
            updates["status"] = "COMPLETED"

        if updates:
            await self._session_repo.update(chat_session.id, updates)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_answer_status(
    result_state: dict[str, Any], graph_status: str
) -> str:
    """Classify a completed turn's outcome for analytics.

    Precedence: an explicit ``answer_status`` set by an answer-producing node
    (faq_node judges its own grounding) wins; otherwise fall back to turn-level
    heuristics.  Failed turns never reach this — ``fail_trace`` stamps FAILED.
    """
    explicit = result_state.get("answer_status")
    if explicit in ("ANSWERED", "PARTIAL", "UNANSWERED", "FAILED"):
        return explicit

    if graph_status == "FAILED":
        return "FAILED"

    # The supervisor could not resolve what the user wants — the turn ends in
    # a clarification ask, which is a non-answer from the user's perspective.
    if (result_state.get("intent") or "").upper() == "UNKNOWN":
        return "UNANSWERED"

    return "ANSWERED"


def user_id_to_uuid(user_id: str) -> UUID:
    """Map a string user-id to a UUID.

    Tries to parse directly as a UUID first (e.g. ``"550e8400-e29b-41d4-…"``).
    Falls back to a deterministic ``uuid5`` derivation so that ``"user_456"``
    always maps to the same stable UUID.  This allows the POC to accept
    arbitrary user-id strings while still satisfying the DB's UUID column.
    """
    try:
        return UUID(user_id)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_OID, user_id)


def parse_session_id(session_id: str | None) -> UUID | None:
    """Parse an optional session-id string into a UUID.

    Returns ``None`` for ``None`` input or any non-UUID string so the caller
    creates a fresh session rather than raising a validation error.
    """
    if session_id is None:
        return None
    try:
        return UUID(session_id)
    except ValueError:
        log.warning("chat.invalid_session_id_ignored", raw=session_id)
        return None


# ── Fit-out draft preview ────────────────────────────────────────────────────

_FITOUT_AGENTS_WITH_PREVIEW: frozenset[str] = frozenset(
    {
        "rdd_fitout_start_agent",
        "rdd_fitout_end_agent",
        "rdd_trading_confirmation_agent",
        "rdd_fitout_progress_agent",
    }
)

# The lease block every fit-out form shows, plus the ids it submits with. The
# ids land in hidden fields, never a display box: the browser writes
# tenant_profile_id into ``company_name`` (the form's id field, as on handover)
# while these forms display the name from ``company``.
_FITOUT_LEASE_PREVIEW_KEYS: tuple[tuple[str, str], ...] = (
    ("lease_brand_mall", "Lease"),
    ("lease_code", "Lease Code"),
    ("company", "Company Name"),
    ("brand", "Brand Name"),
    ("mall", "Mall Name"),
    # The ids the form submits with. A tapped lease chip brings them in its
    # lease card; a typed lease code has no card, so without these the form
    # kept the names only and Submit on it failed with "should provide a valid
    # tenant profile id" (Fitout Progress, open-form mode).
    ("tenant_profile_id", "Tenant Profile Id"),
    ("lease_id", "Lease Id"),
    ("contract_id", "Contract Id"),
    ("property_id", "Property Id"),
    ("brand_id", "Brand Id"),
)

# Fitout Progress's own fields. Limited to this agent on purpose: the three
# confirmations already sync their dates and yes/no radios another way, and
# adding them here would change behaviour nobody reported as broken.
_FITOUT_PROGRESS_PREVIEW_KEYS: tuple[tuple[str, str], ...] = (
    ("fitout_start_date", "Actual Fitout Start Date"),
    ("fitout_end_date", "Original Fitout End Date"),
    ("fitout_progress_percentage", "Fitout Progress %"),
    ("comment", "Comments"),
)


def _fitout_draft_preview_fields(
    active_agent: Any, collected: dict[str, Any]
) -> list[dict[str, Any]]:
    """Rows the browser merges into the draft for a fit-out agent's form."""
    if active_agent not in _FITOUT_AGENTS_WITH_PREVIEW or not collected:
        return []
    keys = list(_FITOUT_LEASE_PREVIEW_KEYS)
    if active_agent == "rdd_fitout_progress_agent":
        keys.extend(_FITOUT_PROGRESS_PREVIEW_KEYS)
    rows: list[dict[str, Any]] = []
    for key, label in keys:
        value = collected.get(key)
        if value is None or value == "" or value == []:
            continue
        rows.append({"key": key, "label": label, "value": value})
    return rows
