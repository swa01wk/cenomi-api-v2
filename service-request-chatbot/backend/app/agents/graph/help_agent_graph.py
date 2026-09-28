"""Compile the Help Agent LangGraph — supervisor + FAQ Q&A + RDD + Work Permit workflows.

Graph shape
-----------
START
  → load_session
  → sr_status_sync         (when sr_id present — syncs platform status)
  → supervisor             (skipped when active_agent already set)
  → faq_node               (Q&A path: ASK_HELP / UNKNOWN intents)
  → registry               (SR action intents: routes to the correct agent)
  → handover_entry         (CREATE_SR — HITL confirmation parser)
  → fm_review_entry        (FM_REVIEW stage boundary)
  → rdd_review_entry       (RDD_REVIEW stage boundary)
  → work_permit_entry      (CREATE_WORK_PERMIT — HITL confirmation parser)
  → fitout_confirm_entry   (FIT_OUT_CONFIRM_* — HITL confirmation parser)
  → field_extraction
  → merge_state
  → lease_lookup
  → validation
  → missing_field
  → confirmation / fm_confirmation / rdd_confirmation / work_permit_confirmation / fitout_confirm_confirmation
  → payload_builder / fm_payload_builder / rdd_payload_builder / work_permit_payload_builder / fitout_confirm_payload_builder
  → api_submission / fm_api_submission / rdd_api_submission / work_permit_api_submission / fitout_confirm_api_submission
  → response_generation
  → save_state
  → END

Agent taxonomy
--------------
- help_agent           : this graph — supervisor (intent routing) + faq_node (Q&A)
- notification_agent   : list / explain / mark-read; OPEN hands off to
                         handover/fitout (sr_status_sync) or sales_data_agent
- rdd_handover_agent           : handles the full RDD lifecycle (CREATE_SR → FM_REVIEW → RDD_REVIEW)
- work_permit_agent            : work permit create (CREATE_WORK_PERMIT) + review hops
- rdd_fitout_start_agent       : fitout start confirmation (FIT_OUT_CONFIRM_FOS)
- rdd_fitout_end_agent         : fitout end confirmation (FIT_OUT_CONFIRM_END)
- rdd_trading_confirmation_agent : trading confirmation (FIT_OUT_CONFIRM_TRADING)

RBAC enforcement
----------------
``_route_after_supervisor`` checks ``ROLE_PERMITTED_INTENTS`` against the
``auth_context`` stored in graph state.  If the classified intent is not
permitted for the user's role, the turn routes directly to
``response_generation`` with a denial message — no SR workflow is activated.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

# ── Shared infrastructure nodes ──────────────────────────────────────────────
from app.agents.graph.nodes.shared.form_action_sync_node import (
    form_action_sync_node,
    is_form_action,
)
from app.agents.graph.nodes.shared.load_session_node import load_session_node
from app.agents.graph.nodes.shared.save_state_node import save_state_node
# supervisor_node lives at nodes/ root for test-patching compatibility
from app.agents.graph.nodes.supervisor_node import (
    _user_wants_preview,
    _user_wants_to_switch,
    supervisor_node,
)
from app.agents.graph.nodes.shared.registry_node import registry_node
from app.agents.graph.nodes.shared.new_request_node import (
    asks_for_a_new_service_request,
    new_request_node,
)
from app.agents.graph.nodes.shared.sr_status_sync_node import sr_status_sync_node
from app.agents.graph.nodes.shared.response_generation_node import response_generation_node
from app.agents.registries.workflow_config import WORKFLOW_CONFIG_REGISTRY, get_workflow_config

# ── FAQ agent nodes ───────────────────────────────────────────────────────────
from app.agents.graph.nodes.faq.faq_node import faq_node
from app.agents.graph.nodes.notification.list_notifications_node import (
    list_notifications_node,
)
from app.agents.graph.nodes.notification.notification_entry_node import (
    notification_entry_node,
)
from app.agents.graph.nodes.notification.open_notification_node import (
    open_notification_node,
)
from app.agents.services.notification_intent import (
    NOTIFICATION_INTENTS,
    classify_notification_intent_from_state,
    should_bypass_for_notification_context,
)

# ── Work Permit agent nodes (CREATE_WORK_PERMIT workflow) ─────────────────────
from app.agents.graph.nodes.work_permit.work_permit_entry_node import (
    WP_DIRECT_SET_CHIP_ACTIONS,
    work_permit_entry_node,
)
from app.agents.graph.nodes.work_permit.work_permit_confirmation_node import work_permit_confirmation_node
from app.agents.graph.nodes.work_permit.work_permit_payload_builder_node import work_permit_payload_builder_node
from app.agents.graph.nodes.work_permit.work_permit_api_submission_node import work_permit_api_submission_node

# ── Document upload node (partitions state["documents"] into FM/RDD buckets) ──
from app.agents.graph.nodes.document_upload_node import document_upload_node

# ── Fitout Confirm agent nodes (FIT_OUT_CONFIRM_FOS / END / TRADING) ──────────
from app.agents.graph.nodes.fitout_confirm.fitout_confirm_entry_node import fitout_confirm_entry_node
from app.agents.graph.nodes.fitout_confirm.fitout_confirm_confirmation_node import fitout_confirm_confirmation_node
from app.agents.graph.nodes.fitout_confirm.fitout_confirm_payload_builder_node import fitout_confirm_payload_builder_node
from app.agents.graph.nodes.fitout_confirm.fitout_confirm_api_submission_node import fitout_confirm_api_submission_node

# ── Sales Data agent nodes ────────────────────────────────────────────────────
from app.agents.graph.nodes.sales_data.sales_data_entry_node import sales_data_entry_node
from app.agents.graph.nodes.sales_data.csv_validation_node import csv_validation_node
from app.agents.graph.nodes.sales_data.csv_field_correction_node import csv_field_correction_node
from app.agents.graph.nodes.sales_data.lease_validation_node import lease_validation_node
from app.agents.graph.nodes.sales_data.stale_notification_node import stale_notification_node
from app.agents.graph.nodes.sales_data.sales_data_confirmation_node import sales_data_confirmation_node
from app.agents.graph.nodes.sales_data.sales_data_payload_builder_node import sales_data_payload_builder_node
from app.agents.graph.nodes.sales_data.sales_data_api_submission_node import sales_data_api_submission_node
from app.agents.graph.nodes.sales_data.submission_success_node import submission_success_node
from app.agents.graph.nodes.sales_data.submission_error_node import submission_error_node
from app.agents.graph.nodes.sales_data.manual_entry_extraction_node import manual_entry_extraction_node
from app.agents.graph.nodes.sales_data.manual_entry_lease_validation_node import manual_entry_lease_validation_node
from app.agents.graph.nodes.sales_data.manual_entry_missing_field_node import manual_entry_missing_field_node
from app.agents.graph.nodes.sales_data.manual_entry_confirmation_node import manual_entry_confirmation_node
from app.agents.graph.nodes.sales_data.manual_entry_submission_node import manual_entry_submission_node
from app.agents.graph.nodes.sales_data.tenant_status_lookup_node import tenant_status_lookup_node
from app.agents.graph.nodes.sales_data.admin_review_entry_node import admin_review_entry_node
from app.agents.graph.nodes.sales_data.pending_months_gate_node import pending_months_gate_node
from app.agents.graph.nodes.sales_data.admin_status_update_node import admin_status_update_node
from app.agents.graph.nodes.sales_data.supporting_doc_upload_node import supporting_doc_upload_node
from app.agents.graph.nodes.sales_data.supporting_doc_confirmation_node import (
    supporting_doc_confirmation_node,
)
from app.agents.graph.nodes.sales_data.supporting_doc_submission_node import (
    supporting_doc_submission_node,
)

# ── RDD agent nodes (CREATE_SR / FM_REVIEW / RDD_REVIEW workflow phases) ──────
from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node
from app.agents.graph.nodes.handover.fm_review_entry_node import fm_review_entry_node
from app.agents.graph.nodes.handover.rdd_review_entry_node import rdd_review_entry_node
from app.agents.services import guided_tour_service as _guided_tour_service
from app.agents.graph.nodes.shared.field_extraction_node import field_extraction_node
from app.agents.graph.nodes.shared.merge_state_node import merge_state_node
from app.agents.graph.nodes.shared.lease_lookup_node import lease_lookup_node
from app.agents.graph.nodes.shared.validation_node import validation_node
from app.agents.graph.nodes.shared.missing_field_node import missing_field_node
from app.agents.graph.nodes.handover.confirmation_node import confirmation_node
from app.agents.graph.nodes.handover.preview_node import preview_node
from app.agents.graph.nodes.handover.payload_builder_node import payload_builder_node
from app.agents.graph.nodes.handover.fm_payload_builder_node import fm_payload_builder_node
from app.agents.graph.nodes.handover.rdd_payload_builder_node import rdd_payload_builder_node
from app.agents.graph.nodes.handover.api_submission_node import api_submission_node
from app.agents.graph.nodes.handover.fm_api_submission_node import fm_api_submission_node
from app.agents.graph.nodes.handover.rdd_api_submission_node import rdd_api_submission_node
from app.agents.graph.state import ServiceRequestGraphState

# ── General Inquiry agent nodes ───────────────────────────────────────────────
from app.agents.graph.nodes.general_inquiry.general_inquiry_entry_node import (
    general_inquiry_entry_node,
)
from app.agents.graph.nodes.general_inquiry.general_inquiry_confirmation_node import (
    general_inquiry_confirmation_node,
)
from app.agents.graph.nodes.general_inquiry.general_inquiry_payload_builder_node import (
    general_inquiry_payload_builder_node,
)
from app.agents.graph.nodes.general_inquiry.general_inquiry_api_submission_node import (
    general_inquiry_api_submission_node,
)

# ── Hoisted from routing-function bodies (previously re-imported on every
# turn — Python caches the module either way, but hiding dependencies inside
# hot functions is unconventional). Aliased because all three OPTIONAL_FIELDS
# symbols share the same name across schema modules.
from app.agents.services.help_interrupt_service import detect_help_interrupt
from app.agents.schemas.fitout_confirm_schema import OPTIONAL_FIELDS as FITOUT_OPTIONAL_FIELDS
from app.agents.schemas.work_permit_schema import OPTIONAL_FIELDS as WP_OPTIONAL_FIELDS
from app.agents.schemas.handover_schema import OPTIONAL_FIELDS as HANDOVER_OPTIONAL_FIELDS
from app.agents.schemas.general_inquiry_schema import OPTIONAL_FIELDS as GI_OPTIONAL_FIELDS

# ---------------------------------------------------------------------------
# Agent entry-node dispatch table
# ---------------------------------------------------------------------------

_AGENT_ENTRY_NODES: dict[str, str] = {
    "rdd_handover_agent": "handover_entry",
    "work_permit_agent": "work_permit_entry",
    "rdd_fitout_start_agent": "fitout_confirm_entry",
    "rdd_fitout_end_agent": "fitout_confirm_entry",
    "rdd_trading_confirmation_agent": "fitout_confirm_entry",
    "sales_data_agent": "sales_data_entry",
    "general_inquiry_agent": "general_inquiry_entry",
}

# Intents that route to the FAQ node (Q&A path — no SR workflow activation)
_FAQ_INTENTS: frozenset[str] = frozenset({"ASK_HELP", "UNKNOWN"})

# Derived from all registered workflow configs — adding a new workflow auto-expands this set.
_SR_ACTION_INTENTS: frozenset[str] = frozenset(
    intent
    for cfg in WORKFLOW_CONFIG_REGISTRY.values()
    for intent in cfg.action_intents
)

# Intents that start a brand-new SR — must not trigger open-existing-SR sync.
_CREATE_NEW_SR_INTENTS: frozenset[str] = frozenset(
    {
        "CREATE_RDD_SERVICE_REQUEST",
        "CREATE_WORK_PERMIT_SR",
        "CREATE_FITOUT_CONFIRM_FOS",
        "CREATE_FITOUT_CONFIRM_END",
        "CREATE_FITOUT_CONFIRM_TRADING",
        # Sales data intents never open an existing platform SR
        "UPLOAD_SALES_DATA",
        "ENTER_SALES_DATA_MANUAL",
        "CHECK_SALES_DATA_STATUS",
        "UPLOAD_SUPPORTING_DOCUMENT",
        "REVIEW_SALES_DATA",
        "APPROVE_SALES_DATA",
        # General Inquiry is always a new SR (no multi-phase review)
        "CREATE_GENERAL_INQUIRY",
    }
)

# Fitout agents share one OPTIONAL_FIELDS schema and one confirmation path.
# Module-level constant (was rebuilt on every _route_after_validation call).
_FITOUT_AGENTS: frozenset[str] = frozenset(
    {"rdd_fitout_start_agent", "rdd_fitout_end_agent", "rdd_trading_confirmation_agent"}
)


def _should_open_existing_sr(state: dict[str, Any]) -> bool:
    """Return True when the turn should sync/open an existing platform SR.

    Predicate (not an intent inclusion list):
      - ``backend_refs.sr_id`` is set
      - sync was not already attempted this turn (prevents supervisor ↔ sync loop)
      - user did not send cancel/switch phrase
      - intent is not a create-new-SR intent
    """
    backend_refs = state.get("backend_refs") or {}
    if not backend_refs.get("sr_id"):
        return False
    if backend_refs.get("sync_attempted"):
        return False
    user_message = state.get("user_message") or ""
    if _user_wants_to_switch(user_message):
        return False
    intent = state.get("intent") or ""
    if intent in _CREATE_NEW_SR_INTENTS:
        return False
    if intent in NOTIFICATION_INTENTS:
        return False
    return True


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


# Fields whose answer is arbitrary prose. While one of these is being
# collected the user's message is CONTENT, so it must never be re-read as a
# new intent — ``_PREVIEW_PHRASES`` matches bare substrings like "review",
# and a comment such as "Please review the details and approve it" was being
# routed to the supervisor, classified as an approve intent, and RBAC-denied
# instead of being stored as the comment.
_FREE_TEXT_ANSWER_FIELDS: frozenset[str] = frozenset(
    {"title", "description", "notes", "comments", "review_comment"}
)


def _route_after_load(state: dict[str, Any]) -> str:
    """Route after document_upload — determines the first live node of a turn.

    Priority order:
    1. form_action_sync  — inline SR-form action
    2. supervisor        — user wants to preview / switch
    3. open_notification — UI tapped Open on a notification
    4. list_notifications / notification — notification context bypass
    5. cancel bypass     — cancel_sr family routes to the active agent entry
    6. new_request       — explicit new-SR request drops the open SR
    7. sr_status_sync    — sr_id present and workflow_stage not terminal
    8. supervisor        — terminal stage or no active agent
    9. faq_node          — help interrupt mid-collection
    10. agent entry node — resume the active agent
    """
    # The step was done on the SR form. The chat reports it and ends the flow;
    # it never repeats it — the create went through the form already, and a
    # second one from the chat was the duplicate.
    if is_form_action(state.get("action_override")):
        return "form_action_sync"
    user_message = state.get("user_message") or ""
    _awaiting_free_text = state.get("active_field") in _FREE_TEXT_ANSWER_FIELDS
    if not _awaiting_free_text and _user_wants_preview(user_message):
        return "supervisor"
    if state.get("active_notification_id") and (state.get("intent") or "") == "OPEN_NOTIFICATION":
        # UI tapped Open on a notification — the routing shim seeds the SR
        # or sales-data handoff (reconciled spec §2.2).
        return "open_notification"
    if not _awaiting_free_text and should_bypass_for_notification_context(state):
        # Notification list/detail/mark-read (including deictic viewing context)
        # must not be swallowed by an already-active SR agent.  Free-text answers
        # (e.g. an SR description mentioning "alerts") stay with the open field.
        notif_intent = classify_notification_intent_from_state(state)
        if notif_intent == "LIST_NOTIFICATIONS":
            return "list_notifications"
        if notif_intent is not None:
            return "notification"
        return "supervisor"

    sr_id = (state.get("backend_refs") or {}).get("sr_id")

    # Cancel-family actions always route to handover_entry regardless of the
    # current workflow_stage.  If we let sr_status_sync run first, it maps the
    # platform operations to FM_REVIEW / RDD_REVIEW and the cancel handler in
    # handover_entry is never reached (fm/rdd entry nodes don't handle cancel_sr).
    _CANCEL_BYPASS_ACTIONS: frozenset[str] = frozenset({
        "cancel_sr", "cancel_create_sr",
        "confirm_cancel_sr", "decline_cancel_sr",
    })
    action = state.get("action_override") or ""
    _GI_REVIEW_ACTIONS: frozenset[str] = frozenset(
        {"confirm_approve_gi", "reject_gi", "ask_more_info_gi", "reassign_gi"}
    )
    # Pending lives on collected_data: backend_refs is restored as sr_id only,
    # so a reject comment typed on the next turn would otherwise miss this
    # branch, reopen the review card, and never PATCH.
    pending_review = str(
        (state.get("backend_refs") or {}).get("gi_pending_action")
        or (state.get("collected_data") or {}).get("gi_pending_action")
        or ""
    )
    if action in _GI_REVIEW_ACTIONS or (
        pending_review and (state.get("user_message") or "").strip()
    ):
        return "general_inquiry_entry"
    if action in _CANCEL_BYPASS_ACTIONS and sr_id:
        agent = state.get("active_agent") or ""
        return _AGENT_ENTRY_NODES.get(agent, "handover_entry")

    if sr_id:
        # An explicit request for a new SR outranks the SR that happens to be
        # open. Sync would hand it to that SR — its preview, or its stage entry
        # (a Mall Manager on the details page of an SR pending FM typed "create
        # hanover" and was refused by the FM review's role guard). new_request
        # drops the open SR and lets the supervisor classify on a clean slate.
        if not _awaiting_free_text and asks_for_a_new_service_request(user_message):
            return "new_request"
        # When the current stage is already terminal the tenant's SR lifecycle is
        # complete.  Running sr_status_sync would advance the stage (e.g. mock
        # backend auto-approves SR_CREATED → APPROVE_SR) before the supervisor has
        # a chance to classify the new user intent.  Skip straight to supervisor so
        # a fresh CREATE_SR intent starts with a clean slate.
        workflow_stage = state.get("workflow_stage") or ""
        all_terminal: frozenset[str] = frozenset(
            s for c in WORKFLOW_CONFIG_REGISTRY.values() for s in c.terminal_stages
        )
        if workflow_stage in all_terminal:
            return "supervisor"
        return "sr_status_sync"

    agent = state.get("active_agent")
    workflow_stage = state.get("workflow_stage") or ""
    # Status lookup is a completed one-shot. Re-enter the supervisor so
    # "I want to upload" is not stuck on CHECK_SALES_DATA_STATUS.
    if (
        agent == "sales_data_agent"
        and workflow_stage in {"STATUS_LOOKUP", "SALES_DATA_COMPLETE"}
        and not action
    ):
        return "supervisor"
    if agent:
        # A help question asked mid-collection must not be swallowed by the
        # open field. Observed live: "Help me with Dashboard" was bound to
        # active_field=lease_code and answered "Sorry, I couldn't find a lease
        # matching that information", and the session could only be recovered by
        # deleting it. faq_node writes only response_message/status, so the
        # draft survives and collection resumes on the next turn.
        if detect_help_interrupt(
            user_message,
            active_agent=agent,
            active_field=state.get("active_field"),
        ):
            return "faq_node"
        return _AGENT_ENTRY_NODES.get(agent, "supervisor")
    return "supervisor"


def _is_background_form_sync(state: dict[str, Any]) -> bool:
    """True for the form bridge's silent patch: edits only, no message or action."""
    return bool(
        (state.get("corrected_fields") or {})
        and not str(state.get("user_message") or "").strip()
        and not state.get("action_override")
    )


def _route_after_sync(state: dict[str, Any]) -> str:
    """After status sync, route to the correct stage entry node.

    Routing is data-driven: each ``WorkflowConfig`` declares which
    ``workflow_stage`` values map to which graph nodes.  Adding a new workflow
    with new stage names requires only a registry entry — no code change here.
    """
    workflow_stage: str = state.get("workflow_stage") or ""
    agent: str = state.get("active_agent") or ""

    # 1. Per-workflow stage → entry node (keyed by active_agent)
    cfg = get_workflow_config(agent)
    if cfg and workflow_stage in cfg.stage_sync_nodes:
        return cfg.stage_sync_nodes[workflow_stage]

    # 2. Scan all configs when active_agent is not yet set
    for registered_cfg in WORKFLOW_CONFIG_REGISTRY.values():
        if workflow_stage in registered_cfg.stage_sync_nodes:
            return registered_cfg.stage_sync_nodes[workflow_stage]

    # 3. Terminal stages → preview when the user is opening an existing SR
    # (sr_id present in backend_refs — set from the request body when the FE
    # calls openExistingSr). Falling through to supervisor here would classify
    # the "Open SR X" bubble as ASK_HELP and return FAQ content instead of the
    # SR card. Only route to supervisor when there is no sr_id (stale state).
    all_terminal: frozenset[str] = frozenset(
        s for c in WORKFLOW_CONFIG_REGISTRY.values() for s in c.terminal_stages
    )
    if workflow_stage in all_terminal:
        # The form re-fetch after a final approve/reject echoes its values back
        # as a silent patch. It is not an "open SR" request, and preview would
        # answer it with an unrequested (Handover-labelled) SR card.
        if _is_background_form_sync(state):
            return "response_generation"
        backend_refs_terminal = state.get("backend_refs") or {}
        if backend_refs_terminal.get("sr_id"):
            return "preview"
        return "supervisor"

    # 4. Fallback: status preview, agent entry, or supervisor (never re-sync)
    backend_refs = state.get("backend_refs") or {}
    if backend_refs.get("sync_attempted") and backend_refs.get("sync_error"):
        return "response_generation"
    if backend_refs.get("sr_id"):
        return "preview"
    if agent:
        return _AGENT_ENTRY_NODES.get(agent, "handover_entry")
    return "supervisor"


def _route_after_open_notification(state: dict[str, Any]) -> str:
    """Continue the open handoff into the domain agent, or surface an error.

    Sales-data notifications have no SR id — they seed ``notification_context``
    and land in ``sales_data_entry``. Handover / fit-out still ride
    ``sr_status_sync``.
    """
    if (state.get("active_agent") or "") == "sales_data_agent":
        return "sales_data_entry"
    refs = state.get("backend_refs") or {}
    if refs.get("notification_context") and not refs.get("sr_id"):
        return "sales_data_entry"
    if refs.get("sr_id"):
        return "sr_status_sync"
    return "response_generation"


def _route_after_supervisor(state: dict[str, Any]) -> str:
    """Route after intent classification.

    Priority:
    1. Open existing SR (sr_id extracted / present) → sr_status_sync (same turn).
    2. WAITING_FOR_USER (RBAC denial, unmatched sales-data phrasing, LLM failure)
       → response_generation.
    3. PREVIEW_SERVICE_REQUEST → preview node.
    4. CHECK_SERVICE_REQUEST_STATUS → preview node (after sync when sr_id set).
    5. ASK_HELP / UNKNOWN → faq_node.
    6. SR action intents → registry.

    RBAC enforcement is performed inside supervisor_node (not here) so that the
    denial message is correctly propagated through graph state.  This function
    is intentionally side-effect-free and only reads state.
    """
    intent: str = state.get("intent") or "UNKNOWN"
    status: str = state.get("status") or ""

    if intent == "LIST_NOTIFICATIONS":
        return "list_notifications"
    if intent == "OPEN_NOTIFICATION":
        return "open_notification"
    if intent in NOTIFICATION_INTENTS:
        return "notification"

    if _should_open_existing_sr(state):
        return "sr_status_sync"

    # Supervisor already composed a user-facing reply (RBAC denial, unmatched
    # sales-data phrasing, LLM failure). Do not send that turn to preview.
    if status == "WAITING_FOR_USER":
        return "response_generation"

    # Preview always routes to the dedicated preview node
    if intent == "PREVIEW_SERVICE_REQUEST":
        return "preview"

    # CHECK_SERVICE_REQUEST_STATUS — preview after sync when sr_id is present;
    # otherwise preview_node may GET or show draft.
    if intent == "CHECK_SERVICE_REQUEST_STATUS":
        return "preview"

    # ── Q&A path ──────────────────────────────────────────────────────────────
    if intent in _FAQ_INTENTS:
        return "faq_node"

    # ── SR action path ────────────────────────────────────────────────────────
    if intent in _SR_ACTION_INTENTS:
        return "registry"

    # Fallback — unknown intent shape, send to FAQ
    return "faq_node"


def _route_after_registry(state: dict[str, Any]) -> str:
    """Continue to the correct agent entry node."""
    if state.get("status") == "WAITING_FOR_USER":
        return "response_generation"
    agent = state.get("active_agent")
    return _AGENT_ENTRY_NODES.get(agent or "", "handover_entry")


def _route_after_merge(state: dict[str, Any]) -> str:
    if state.get("selected_lease"):
        return "lease_lookup"
    # Review / approve stages: lease was resolved at create time.
    workflow_stage = state.get("workflow_stage") or ""
    if workflow_stage in (
        "FM_REVIEW",
        "RDD_REVIEW",
        "APPROVE_SR",
        "WP_DD_REVIEW",
        "WP_FM_REVIEW",
        "WP_MM_REVIEW",
        "WP_MM_OPS_REVIEW",
        "GI_CRT_REVIEW",
    ):
        return "validation"
    collected = state.get("collected_data") or {}
    if not collected.get("lease_id"):
        return "lease_lookup"
    return "validation"


def _route_after_lease(state: dict[str, Any]) -> str:
    if state.get("status") == "WAITING_FOR_USER":
        return "response_generation"
    return "validation"


def _route_after_validation(state: dict[str, Any]) -> str:
    """Route after validation — data-driven via WorkflowConfig.

    Adding a new workflow with new collection stages and confirmation nodes
    requires only a registry entry plus a get_missing_fields dispatch below.
    """
    active_agent_for_optional = state.get("active_agent") or ""
    OPTIONAL_FIELDS = (
        FITOUT_OPTIONAL_FIELDS
        if active_agent_for_optional in _FITOUT_AGENTS
        else WP_OPTIONAL_FIELDS
        if active_agent_for_optional == "work_permit_agent"
        else GI_OPTIONAL_FIELDS
        if active_agent_for_optional == "general_inquiry_agent"
        else HANDOVER_OPTIONAL_FIELDS
    )

    # A pending "change field X" redirect must reach missing_field_node, which
    # owns the re-ask. Without this the routing below sends a fully-collected
    # draft straight to confirmation, the signal is never consumed, and the
    # user's correction request is silently dropped — precisely when they are
    # reviewing the finished draft and most want to fix something.
    if state.get("correction_pending_field"):
        return "missing_field"

    already_confirmed = state.get("confirmation_status") == "CONFIRMED"

    # When the user has already explicitly confirmed (e.g. via confirm_fm_approve
    # or confirm_rdd_submit), "required" type blocking errors for OPTIONAL fields
    # (e.g. comments=None) must not block the flow.  Non-optional and non-required
    # blocking errors (date_range, enum, document_count, …) still block as normal.
    # Optional required fields never block — the user may leave them blank and they
    # will appear on the confirmation card for optional editing.
    blocking = [
        e for e in (state.get("validation_errors") or [])
        if e.get("blocking", True)
        and not (
            e.get("validation_type") == "required"
            and e.get("field") in OPTIONAL_FIELDS
        )
    ]
    if blocking:
        return "missing_field"

    original_stage = state.get("workflow_stage") or "CREATE_SR"
    active_agent: str = state.get("active_agent") or ""
    cfg = get_workflow_config(active_agent)

    # Resolve collection stages and confirmation nodes from WorkflowConfig.
    # Fall back to rdd_agent defaults when no config is found.
    collection_stages: frozenset[str] = (
        cfg.collection_stages
        if cfg
        else frozenset({"CREATE_SR", "FM_REVIEW", "RDD_REVIEW"})
    )
    confirmation_nodes: dict[str, str] = (
        cfg.confirmation_nodes
        if cfg
        else {"CREATE_SR": "confirmation", "FM_REVIEW": "fm_confirmation", "RDD_REVIEW": "rdd_confirmation"}
    )

    if original_stage not in collection_stages:
        return "response_generation"

    # Dispatch get_missing_fields to the correct agent schema.
    collected = state.get("collected_data") or {}
    if active_agent == "work_permit_agent":
        from app.agents.schemas.work_permit_schema import get_missing_fields as wp_missing
        still_missing = wp_missing(original_stage, collected)
    elif active_agent in _FITOUT_AGENTS:
        from app.agents.schemas.fitout_confirm_schema import get_missing_fields as fc_missing
        sub_cat = state.get("sub_category") or ""
        still_missing = fc_missing(original_stage, sub_cat, collected)
    elif active_agent == "general_inquiry_agent":
        from app.agents.schemas.general_inquiry_schema import get_missing_fields as gi_missing
        still_missing = gi_missing(original_stage, collected)
    else:
        from app.agents.schemas.handover_schema import get_missing_fields as rdd_missing
        still_missing = rdd_missing(original_stage, collected)

    if still_missing:
        # Non-optional missing fields always route to missing_field first.
        non_optional_missing = [f for f in still_missing if f not in OPTIONAL_FIELDS]
        if non_optional_missing:
            return "missing_field"

        # CREATE_SR (handover agents only): proactively ask for optional fields
        # (notes, comments, etc.) once before the confirmation card.
        # Tier 1/2 test scripts supply these turn-by-turn, so we must ask.
        # Fitout agents (FOS/END/TRADING) must NOT proactively ask for optional
        # comments — their packed smoke tests expect the confirmation card directly.
        # All other stages (CREATE_HANDED_OVER_SR, FM_REVIEW, RDD_REVIEW): skip
        # proactive optional asking and route directly to the confirmation card.
        #
        # Guard: skip this proactive ask when the user has already confirmed
        # (already_confirmed=True).  On the confirmed path the optional fields
        # that were never supplied will be auto-set to "" by merge_state_node,
        # so routing back to missing_field would ask for comments AFTER the user
        # already clicked Confirm — which is incorrect.
        # Extend proactive optional asking to CREATE_HANDED_OVER_SR (Path B) so
        # comments (and any other optional-only fields) are asked once before the
        # review card, matching the CREATE_SR (Path A) behavior.
        if (
            original_stage
            in ("CREATE_SR", "CREATE_HANDED_OVER_SR", "CREATE_WORK_PERMIT")
            and active_agent not in _FITOUT_AGENTS
            and not already_confirmed
        ):
            optional_never_asked = [
                f for f in still_missing
                if f in OPTIONAL_FIELDS and collected.get(f) is None
            ]
            if optional_never_asked:
                return "missing_field"

    return confirmation_nodes.get(original_stage, "confirmation")


# Chip actions that write directly into
# ``collected_data`` inside handover_entry_node (see
# _HANDOVER_TYPE_ACTIONS / _INSPECTION_DONE_BY_ACTIONS) — the value is already
# final, so routing through field_extraction is not just redundant, it is
# actively wrong: the LLM re-extraction pass usually finds nothing new in the
# chip's label text, and field_extraction's "correction re-arm" safety net
# then reads "field already has a value + nothing extracted this turn" as a
# failed correction attempt and re-asks the question the user just answered.
_DIRECT_SET_CHIP_ACTIONS: frozenset[str] = frozenset(
    {
        "select_handover_type_already_trading",
        "select_handover_type_initiate_handover",
        "select_inspection_done_by_fm_manager",
        "select_inspection_done_by_operations",
    }
)

_WP_DIRECT_SET_CHIP_ACTIONS: frozenset[str] = WP_DIRECT_SET_CHIP_ACTIONS

# Same idea for the RDD_REVIEW stage — rdd_review_entry_node writes
# ``document_status`` straight into collected_data for these two chips.
_RDD_DIRECT_SET_CHIP_ACTIONS: frozenset[str] = frozenset(
    {
        "select_rdd_status_approved",
        "select_rdd_status_approved_with_changes",
    }
)


def _form_patch_target(state: dict[str, Any]) -> str | None:
    """Where a turn carrying inline form edits belongs, or ``None``.

    ``corrected_fields`` is the form bridge's silent patch: the user typed into
    the SR form and the edit is riding back to the draft. Only ``merge_state``
    writes it into ``collected_data``, so such a turn must never be
    short-circuited to ``response_generation`` — that node cannot write the
    draft, so the edit is dropped without a trace and the next apply pushes the
    assistant's older value back over what the user typed.

    Live symptom: with the form left open, edits made while the document card
    or the review card was up (both idle waits with no ``active_field``)
    vanished, and the review card showed the values the assistant had collected
    rather than the ones on screen.

    A patch that carries no message has nothing for the extractor to read, so
    it goes straight to ``merge_state``; one that arrives alongside a typed
    message still goes through extraction, which flows into ``merge_state``
    anyway.
    """
    if not (state.get("corrected_fields") or {}):
        return None
    return (
        "field_extraction"
        if str(state.get("user_message") or "").strip()
        else "merge_state"
    )


def _waiting_short_circuit_target(state: dict[str, Any]) -> str:
    """Where a turn goes when the persisted ``status`` is WAITING_FOR_USER.

    Every question the bot asks saves WAITING_FOR_USER, so that value is present
    on the *next* turn — the one carrying the user's reply. Short-circuiting
    straight to response_generation therefore swallows the two messages that
    most need to reach the graph:

    - a correction request ("i want to edit the fitout start date"), and
    - the answer to the question that status belongs to (``active_field``).

    response_generation cannot write ``collected_data``, so the LLM just
    improvises from the values already there — observed live in RDD_REVIEW as
    "I see you want to change the fitout start date to 28/08/2026" (quoting the
    *current* value back), after which every date the user typed was discarded
    and the same question returned forever.

    Anything else — a genuine idle wait such as the MM optional-upload offer,
    which leaves ``active_field`` None — still short-circuits as before.
    """
    from app.agents.services.field_correction_service import detect_correction_intent

    patch_target = _form_patch_target(state)
    if patch_target:
        return patch_target

    if detect_correction_intent(
        state.get("user_message") or "",
        state.get("collected_data") or {},
        state.get("active_field"),
        state.get("workflow_stage"),
    ):
        return "field_extraction"
    if state.get("active_field"):
        return "field_extraction"
    return "response_generation"


def _route_after_handover_entry(state: dict[str, Any]) -> str:
    # Check confirmation_status BEFORE stale WAITING_FOR_USER so that a confirm
    # action set this turn is never shadowed by a persisted WAITING_FOR_USER value
    # from the previous save_state.  Mirrors the same guard in _route_after_fm_entry.

    # The guided-tour choice is not a collectable field, so extraction has
    # nothing to bind it to. Chip or typed, it goes straight to merge_state,
    # which records it for every workflow.
    if _guided_tour_service.has_pending_answer(state):
        return "merge_state"
    if state.get("confirmation_status") == "CONFIRMED":
        return "merge_state"
    if state.get("action_override") == "continue_milestone":
        return "merge_state"
    if state.get("action_override") in _DIRECT_SET_CHIP_ACTIONS:
        return "merge_state"
    # When REJECTED (e.g. after cancel+edit), always route through field_extraction
    # to process the correction, bypassing a stale WAITING_FOR_USER saved from the
    # prior turn's "what would you like to change?" message.
    if state.get("confirmation_status") == "REJECTED":
        return "field_extraction"
    # Optional MM upload offer/wait and other WAITING short-circuits.
    if state.get("status") == "WAITING_FOR_USER":
        return _waiting_short_circuit_target(state)
    if state.get("action_override") == "cancel":
        return "merge_state"
    return _form_patch_target(state) or "field_extraction"


def _route_after_fm_entry(state: dict[str, Any]) -> str:
    # Check fm_action / confirmation_status BEFORE stale status so that an
    # approve or confirm action set this turn is never shadowed by a persisted
    # WAITING_FOR_USER value from the previous save_state.

    # The guided-tour choice is not a collectable field, so extraction has
    # nothing to bind it to. Chip or typed, it goes straight to merge_state,
    # which records it for every workflow.
    if _guided_tour_service.has_pending_answer(state):
        return "merge_state"
    if state.get("confirmation_status") == "CONFIRMED":
        return "merge_state"
    backend_refs = state.get("backend_refs") or {}
    if backend_refs.get("fm_action"):
        return "merge_state"
    if state.get("status") == "WAITING_FOR_USER":
        return _waiting_short_circuit_target(state)
    return _form_patch_target(state) or "field_extraction"


def _route_after_rdd_entry(state: dict[str, Any]) -> str:
    # Confirmation / action checks come BEFORE the stale-status short circuit,
    # so an action set this turn is never shadowed by the WAITING_FOR_USER value
    # persisted by the previous save_state. Same ordering as _route_after_fm_entry
    # and _route_after_handover_entry.
    #
    # Single-turn confirm already set CONFIRMED — skip extraction and go
    # straight through merge → validation → rdd_confirmation → payload builder.

    # The guided-tour choice is not a collectable field, so extraction has
    # nothing to bind it to. Chip or typed, it goes straight to merge_state,
    # which records it for every workflow.
    if _guided_tour_service.has_pending_answer(state):
        return "merge_state"
    if state.get("confirmation_status") == "CONFIRMED":
        return "merge_state"
    # Status chips write document_status into collected_data directly (see
    # rdd_review_entry_node._STATUS_ACTIONS), so the value is already final.
    # Same reasoning as _DIRECT_SET_CHIP_ACTIONS on the handover entry node:
    # re-running extraction over the chip label finds nothing, and
    # field_extraction's correction re-arm then reads "field has a value +
    # nothing extracted" as a failed correction and re-asks the question the
    # user just answered.
    if state.get("action_override") in _RDD_DIRECT_SET_CHIP_ACTIONS:
        return "merge_state"
    backend_refs = state.get("backend_refs") or {}
    if backend_refs.get("rdd_action"):
        return "merge_state"
    # Report already on platform — idle open should show Final Approve chips,
    # not re-enter field collection / submit confirmation.
    if backend_refs.get("rdd_status") == "REPORT_SUBMITTED":
        return "response_generation"
    if state.get("status") == "WAITING_FOR_USER":
        return _waiting_short_circuit_target(state)
    return _form_patch_target(state) or "field_extraction"


def _route_after_confirmation(state: dict[str, Any]) -> str:
    if state.get("confirmation_status") == "CONFIRMED":
        return "payload_builder"
    return "response_generation"


def _route_after_fm_confirmation(state: dict[str, Any]) -> str:
    if state.get("confirmation_status") == "CONFIRMED":
        return "fm_payload_builder"
    return "response_generation"


def _route_after_rdd_confirmation(state: dict[str, Any]) -> str:
    if state.get("confirmation_status") == "CONFIRMED":
        return "rdd_payload_builder"
    return "response_generation"


def _route_after_work_permit_entry(state: dict[str, Any]) -> str:
    # The guided-tour choice is not a collectable field, so extraction has
    # nothing to bind it to. Chip or typed, it goes straight to merge_state,
    # which records it for every workflow.
    if _guided_tour_service.has_pending_answer(state):
        return "merge_state"
    # Mirror fitout/handover: CONFIRMED must win over a stale WAITING_FOR_USER
    # left from the confirmation-card turn.
    if state.get("confirmation_status") == "CONFIRMED":
        return "merge_state"
    if state.get("action_override") in _WP_DIRECT_SET_CHIP_ACTIONS:
        return "merge_state"
    if state.get("confirmation_status") == "REJECTED":
        return "field_extraction"
    if state.get("status") == "WAITING_FOR_USER":
        return _form_patch_target(state) or "response_generation"
    if state.get("action_override") == "cancel":
        return "merge_state"
    workflow_stage = state.get("workflow_stage") or ""
    if workflow_stage in (
        "WP_DD_REVIEW",
        "WP_FM_REVIEW",
        "WP_MM_REVIEW",
        "WP_MM_OPS_REVIEW",
    ):
        return "merge_state"
    return _form_patch_target(state) or "field_extraction"


def _route_after_work_permit_confirmation(state: dict[str, Any]) -> str:
    if state.get("confirmation_status") == "CONFIRMED":
        return "work_permit_payload_builder"
    return "response_generation"


def _route_after_fitout_confirm_entry(state: dict[str, Any]) -> str:
    # Check confirmation_status BEFORE stale WAITING_FOR_USER, mirroring
    # _route_after_handover_entry / _route_after_fm_entry. The turn that renders
    # the confirmation card ends in response_generation, which always leaves
    # status="WAITING_FOR_USER" persisted. On the NEXT turn the user clicks
    # Confirm & Submit; fitout_confirm_entry_node sets confirmation_status=
    # CONFIRMED but that stale status is still in state. Testing status first
    # short-circuited to response_generation, which re-emitted the previous
    # question instead of submitting — the flow looped on the date prompt and
    # never produced a success message or View Service Request button.

    # The guided-tour choice is not a collectable field, so extraction has
    # nothing to bind it to. Chip or typed, it goes straight to merge_state,
    # which records it for every workflow.
    if _guided_tour_service.has_pending_answer(state):
        return "merge_state"
    if state.get("confirmation_status") == "CONFIRMED":
        return "merge_state"
    if state.get("action_override") == "continue_milestone":
        return "merge_state"
    if state.get("confirmation_status") == "REJECTED":
        return "field_extraction"
    if state.get("status") == "WAITING_FOR_USER":
        return _form_patch_target(state) or "response_generation"
    if state.get("action_override") == "cancel":
        return "merge_state"
    return _form_patch_target(state) or "field_extraction"


def _route_after_fitout_confirm_confirmation(state: dict[str, Any]) -> str:
    if state.get("confirmation_status") == "CONFIRMED":
        return "fitout_confirm_payload_builder"
    return "response_generation"


# ---------------------------------------------------------------------------
# General Inquiry Agent routing
# ---------------------------------------------------------------------------


def _route_after_general_inquiry_entry(state: dict[str, Any]) -> str:
    """Route after the general_inquiry_entry_node.

    A lease chip or a typed lease code arrives while status is still
    WAITING_FOR_USER from the question that asked for it. Sending that turn
    straight to response_generation skips lease lookup, so the model only
    talks about the code. Handover runs the same reply through extraction
    and lease lookup first.
    """
    if state.get("confirmation_status") == "CONFIRMED":
        return "merge_state"
    if state.get("response_message") and (
        (state.get("workflow_stage") or "") == "GI_CRT_REVIEW"
        or (state.get("backend_refs") or {}).get("gi_pending_action")
        or (state.get("collected_data") or {}).get("gi_pending_action")
    ):
        return "response_generation"
    if _guided_tour_service.has_pending_answer(state):
        return "merge_state"
    if state.get("confirmation_status") == "REJECTED":
        return "field_extraction"
    if state.get("status") == "CANCELLED":
        return "response_generation"
    # Chip selection carries the lease id. Merge keeps it, then lease lookup
    # resolves the row the same way a handover chip does.
    if state.get("selected_lease"):
        return "merge_state"
    if state.get("status") == "WAITING_FOR_USER":
        return _waiting_short_circuit_target(state)
    return _form_patch_target(state) or "field_extraction"


def _route_after_general_inquiry_confirmation(state: dict[str, Any]) -> str:
    """Route after the general_inquiry_confirmation_node."""
    if state.get("confirmation_status") == "CONFIRMED":
        return "general_inquiry_payload_builder"
    return "response_generation"


# ---------------------------------------------------------------------------
# Sales Data Agent routing
# ---------------------------------------------------------------------------


def _route_after_sales_data_entry(state: dict[str, Any]) -> str:
    """Route after the sales_data_entry_node based on workflow_stage."""
    workflow_stage: str = state.get("workflow_stage") or ""
    status: str = state.get("status") or ""

    if status == "WAITING_FOR_USER":
        return "response_generation"

    route_map = {
        "CSV_LAYER1_VALIDATION": "csv_validation",
        "CSV_CORRECTION": "csv_field_correction",  # user is providing missing sales_amt values
        "MANUAL_ENTRY": "manual_entry_extraction",
        "STATUS_LOOKUP": "tenant_status_lookup",
        "ADMIN_REVIEW": "admin_review_entry",
        "ADMIN_CONFIRM": "admin_review_entry",
        "SUBMITTING": "sales_data_payload_builder",
        "AWAITING_TABLE_CONFIRM": "sales_data_confirmation",
        "AWAITING_MANUAL_CONFIRM": "manual_entry_confirmation",
        "PENDING_MONTHS_CHECK": "pending_months_gate",
        "AWAITING_PENDING_MONTHS_CHOICE": "pending_months_gate",
        "AWAITING_OTHER_MONTHS_REMINDER": "pending_months_gate",
        "DOC_SELECT": "supporting_doc_upload",
        "AWAITING_DOC_UPLOAD": "supporting_doc_upload",
        "DOC_UPLOADING": "supporting_doc_upload",
        "AWAITING_DOC_CONFIRM": "supporting_doc_confirmation",
        "DOC_SUBMITTING": "supporting_doc_submission",
        # Defensive: normally reached via lease_validation → stale_notification,
        # but a reconnect with this workflow_stage persisted must not get stuck
        # in response_generation on re-entry through sales_data_entry.
        "STALE_NOTIFICATION": "stale_notification",
    }
    return route_map.get(workflow_stage, "response_generation")


def _route_after_csv_validation(state: dict[str, Any]) -> str:
    # CSV_CORRECTION is a WAITING_FOR_USER state — the correction happens on the
    # next turn via csv_field_correction, reached through sales_data_entry's
    # route map, not from here.
    if state.get("status") == "WAITING_FOR_USER":
        return "response_generation"
    if (state.get("workflow_stage") or "") == "CSV_LAYER2_VALIDATION":
        return "lease_validation"
    return "response_generation"


def _route_after_csv_field_correction(state: dict[str, Any]) -> str:
    if state.get("status") == "WAITING_FOR_USER":
        return "response_generation"
    workflow_stage = state.get("workflow_stage") or ""
    if workflow_stage == "CSV_LAYER2_VALIDATION":
        return "lease_validation"
    return "response_generation"


def _route_after_lease_validation(state: dict[str, Any]) -> str:
    workflow_stage = state.get("workflow_stage") or ""
    if workflow_stage == "STALE_NOTIFICATION":
        return "stale_notification"
    if workflow_stage == "AWAITING_TABLE_CONFIRM":
        return "sales_data_confirmation"
    if workflow_stage == "AWAITING_PERIOD_MISMATCH_CONFIRM":
        return "response_generation"
    return "response_generation"


def _route_after_sales_data_confirmation(state: dict[str, Any]) -> str:
    if state.get("confirmation_status") == "CONFIRMED":
        return "sales_data_payload_builder"
    return "response_generation"


def _route_after_sales_data_payload_builder(state: dict[str, Any]) -> str:
    if state.get("status") == "WAITING_FOR_USER":
        return "response_generation"
    return "sales_data_api_submission"


def _route_after_sales_data_api_submission(state: dict[str, Any]) -> str:
    workflow_stage = state.get("workflow_stage") or ""
    if workflow_stage == "SALES_DATA_COMPLETE":
        return "submission_success"
    if workflow_stage == "SUBMISSION_ERROR":
        return "submission_error"
    return "response_generation"


def _route_after_manual_entry_extraction(state: dict[str, Any]) -> str:
    collected: dict = state.get("collected_data") or {}
    required = ["lease_code", "sales_period_start", "sales_period_end", "sales_amt"]
    if all(collected.get(f) for f in required):
        return "manual_entry_lease_validation"
    return "manual_entry_missing_field"


def _route_after_manual_entry_lease_validation(state: dict[str, Any]) -> str:
    workflow_stage = state.get("workflow_stage") or ""
    if state.get("status") == "WAITING_FOR_USER":
        return "response_generation"
    if workflow_stage == "AWAITING_MANUAL_CONFIRM":
        return "manual_entry_confirmation"
    return "response_generation"


def _route_after_manual_entry_confirmation(state: dict[str, Any]) -> str:
    if state.get("confirmation_status") == "CONFIRMED" or state.get("workflow_stage") == "SUBMITTING":
        return "manual_entry_submission"
    return "response_generation"


def _route_after_supporting_doc_upload(state: dict[str, Any]) -> str:
    if state.get("status") == "WAITING_FOR_USER":
        return "response_generation"
    if state.get("workflow_stage") == "AWAITING_DOC_CONFIRM":
        return "supporting_doc_confirmation"
    return "response_generation"


def _route_after_supporting_doc_confirmation(state: dict[str, Any]) -> str:
    if (
        state.get("confirmation_status") == "CONFIRMED"
        or state.get("workflow_stage") == "DOC_SUBMITTING"
    ):
        return "supporting_doc_submission"
    return "response_generation"


def _route_after_manual_entry_submission(state: dict[str, Any]) -> str:
    workflow_stage = state.get("workflow_stage") or ""
    if workflow_stage == "SALES_DATA_COMPLETE":
        return "submission_success"
    if workflow_stage == "SUBMISSION_ERROR":
        return "submission_error"
    return "response_generation"


def _route_after_admin_review(state: dict[str, Any]) -> str:
    workflow_stage = state.get("workflow_stage") or ""
    confirmation_status = state.get("confirmation_status") or ""

    if confirmation_status == "CONFIRMED" or workflow_stage == "ADMIN_SUBMITTING":
        return "admin_status_update"
    return "response_generation"  # covers WAITING_FOR_USER and all other cases


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_help_agent_graph():
    """Build and compile the full Help Agent LangGraph.

    The Help Agent owns five concerns:
    - FAQ / Q&A path (``faq_node``) for ASK_HELP / UNKNOWN intents
    - RDD handover lifecycle routing (``rdd_handover_agent``) for handover SR action intents
    - Work Permit routing (``work_permit_agent``) for work permit SR creation
    - Fitout confirmation routing (``rdd_fitout_start/end_agent``, ``rdd_trading_confirmation_agent``)
    - Sales Data Agent routing (``sales_data_agent``) for tenant CSV/manual sales
      data upload and admin review/approve
    """
    graph = StateGraph(ServiceRequestGraphState)

    # ── Node registration ────────────────────────────────────────────────────
    graph.add_node("load_session", load_session_node)
    graph.add_node("document_upload", document_upload_node)
    graph.add_node("sr_status_sync", sr_status_sync_node)
    graph.add_node("new_request", new_request_node)
    graph.add_node("form_action_sync", form_action_sync_node)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("faq_node", faq_node)
    graph.add_node("notification", notification_entry_node)
    graph.add_node("list_notifications", list_notifications_node)
    graph.add_node("open_notification", open_notification_node)
    graph.add_node("preview", preview_node)
    graph.add_node("registry", registry_node)
    graph.add_node("handover_entry", handover_entry_node)
    graph.add_node("fm_review_entry", fm_review_entry_node)
    graph.add_node("rdd_review_entry", rdd_review_entry_node)
    graph.add_node("field_extraction", field_extraction_node)
    graph.add_node("merge_state", merge_state_node)
    graph.add_node("lease_lookup", lease_lookup_node)
    graph.add_node("validation", validation_node)
    graph.add_node("missing_field", missing_field_node)
    graph.add_node("confirmation", confirmation_node)
    graph.add_node("fm_confirmation", confirmation_node)
    graph.add_node("rdd_confirmation", confirmation_node)
    graph.add_node("payload_builder", payload_builder_node)
    graph.add_node("fm_payload_builder", fm_payload_builder_node)
    graph.add_node("rdd_payload_builder", rdd_payload_builder_node)
    graph.add_node("api_submission", api_submission_node)
    graph.add_node("fm_api_submission", fm_api_submission_node)
    graph.add_node("rdd_api_submission", rdd_api_submission_node)
    # ── Work Permit agent nodes ───────────────────────────────────────────────
    graph.add_node("work_permit_entry", work_permit_entry_node)
    graph.add_node("work_permit_confirmation", work_permit_confirmation_node)
    graph.add_node("work_permit_payload_builder", work_permit_payload_builder_node)
    graph.add_node("work_permit_api_submission", work_permit_api_submission_node)
    # ── Fitout Confirm agent nodes ────────────────────────────────────────────
    graph.add_node("fitout_confirm_entry", fitout_confirm_entry_node)
    graph.add_node("fitout_confirm_confirmation", fitout_confirm_confirmation_node)
    graph.add_node("fitout_confirm_payload_builder", fitout_confirm_payload_builder_node)
    graph.add_node("fitout_confirm_api_submission", fitout_confirm_api_submission_node)
    # ── General Inquiry agent nodes ───────────────────────────────────────────
    graph.add_node("general_inquiry_entry", general_inquiry_entry_node)
    graph.add_node("general_inquiry_confirmation", general_inquiry_confirmation_node)
    graph.add_node("general_inquiry_payload_builder", general_inquiry_payload_builder_node)
    graph.add_node("general_inquiry_api_submission", general_inquiry_api_submission_node)
    # ── Sales Data agent nodes ─────────────────────────────────────────────────
    graph.add_node("sales_data_entry", sales_data_entry_node)
    graph.add_node("pending_months_gate", pending_months_gate_node)
    graph.add_node("csv_validation", csv_validation_node)
    graph.add_node("lease_validation", lease_validation_node)
    graph.add_node("stale_notification", stale_notification_node)
    graph.add_node("sales_data_confirmation", sales_data_confirmation_node)
    graph.add_node("sales_data_payload_builder", sales_data_payload_builder_node)
    graph.add_node("sales_data_api_submission", sales_data_api_submission_node)
    graph.add_node("submission_success", submission_success_node)
    graph.add_node("submission_error", submission_error_node)
    graph.add_node("csv_field_correction", csv_field_correction_node)
    graph.add_node("manual_entry_extraction", manual_entry_extraction_node)
    graph.add_node("manual_entry_lease_validation", manual_entry_lease_validation_node)
    graph.add_node("manual_entry_missing_field", manual_entry_missing_field_node)
    graph.add_node("manual_entry_confirmation", manual_entry_confirmation_node)
    graph.add_node("manual_entry_submission", manual_entry_submission_node)
    graph.add_node("tenant_status_lookup", tenant_status_lookup_node)
    graph.add_node("admin_review_entry", admin_review_entry_node)
    graph.add_node("admin_status_update", admin_status_update_node)
    graph.add_node("supporting_doc_upload", supporting_doc_upload_node)
    graph.add_node("supporting_doc_confirmation", supporting_doc_confirmation_node)
    graph.add_node("supporting_doc_submission", supporting_doc_submission_node)
    graph.add_node("response_generation", response_generation_node)
    graph.add_node("save_state", save_state_node)

    # ── Entry point ──────────────────────────────────────────────────────────
    graph.add_edge(START, "load_session")

    # ── Session / status-sync routing ────────────────────────────────────────
    # document_upload_node runs immediately after load_session on every turn so
    # that backend_refs.uploaded_documents and backend_refs.rdd_document_id are
    # populated from state["documents"] before any payload builder is reached.
    graph.add_edge("load_session", "document_upload")
    graph.add_conditional_edges(
        "document_upload",
        _route_after_load,
        {
            "sr_status_sync": "sr_status_sync",
            "new_request": "new_request",
            "form_action_sync": "form_action_sync",
            "supervisor": "supervisor",
            "handover_entry": "handover_entry",
            "work_permit_entry": "work_permit_entry",
            "fitout_confirm_entry": "fitout_confirm_entry",
            "sales_data_entry": "sales_data_entry",
            "general_inquiry_entry": "general_inquiry_entry",
            # Help question asked mid-collection — answered without disturbing
            # the draft (see detect_help_interrupt).
            "faq_node": "faq_node",
            "notification": "notification",
            "list_notifications": "list_notifications",
            "open_notification": "open_notification",
        },
    )
    # A new request raised from an open SR: its context is dropped, then the
    # supervisor classifies the message as it would on a fresh session.
    graph.add_edge("new_request", "supervisor")
    # The form already did the step: reply and save, nothing in between.
    graph.add_edge("form_action_sync", "save_state")
    graph.add_conditional_edges(
        "sr_status_sync",
        _route_after_sync,
        {
            "handover_entry": "handover_entry",
            "fm_review_entry": "fm_review_entry",
            "rdd_review_entry": "rdd_review_entry",
            "fitout_confirm_entry": "fitout_confirm_entry",
            "work_permit_entry": "work_permit_entry",
            "preview": "preview",
            "supervisor": "supervisor",
            # Sync GET failure sets sync_error and returns response_generation
            # (_route_after_sync); must be registered or LangGraph raises KeyError.
            "response_generation": "response_generation",
        },
    )

    # ── Intent routing ────────────────────────────────────────────────────────
    graph.add_conditional_edges(
        "supervisor",
        _route_after_supervisor,
        {
            "sr_status_sync": "sr_status_sync",
            "faq_node": "faq_node",
            "preview": "preview",
            "registry": "registry",
            "response_generation": "response_generation",
            "notification": "notification",
            "list_notifications": "list_notifications",
            "open_notification": "open_notification",
        },
    )
    # FAQ answers are self-contained — skip the SR-workflow rewrite step to
    # save one LLM call (~200–400 ms) and preserve inline [Source: ...] markers.
    graph.add_edge("faq_node", "save_state")
    graph.add_edge("notification", "save_state")
    graph.add_edge("list_notifications", "save_state")
    # Spec §2.2: open_notification → sr_status_sync (SR) or sales_data_entry
    # (lease-code notifications). The error turn (no id) branches to
    # response_generation so the message reaches the user instead of an
    # sr-less sync no-op.
    graph.add_conditional_edges(
        "open_notification",
        _route_after_open_notification,
        {
            "sr_status_sync": "sr_status_sync",
            "sales_data_entry": "sales_data_entry",
            "response_generation": "response_generation",
        },
    )
    graph.add_edge("preview", "response_generation")
    graph.add_conditional_edges(
        "registry",
        _route_after_registry,
        {
            "handover_entry": "handover_entry",
            "work_permit_entry": "work_permit_entry",
            "fitout_confirm_entry": "fitout_confirm_entry",
            "sales_data_entry": "sales_data_entry",
            "general_inquiry_entry": "general_inquiry_entry",
            "response_generation": "response_generation",
        },
    )

    # ── Stage entry nodes ─────────────────────────────────────────────────────
    graph.add_conditional_edges(
        "handover_entry",
        _route_after_handover_entry,
        {
            "field_extraction": "field_extraction",
            "merge_state": "merge_state",
            "response_generation": "response_generation",
        },
    )
    graph.add_conditional_edges(
        "fm_review_entry",
        _route_after_fm_entry,
        {
            "field_extraction": "field_extraction",
            "merge_state": "merge_state",
            "response_generation": "response_generation",
        },
    )
    graph.add_conditional_edges(
        "rdd_review_entry",
        _route_after_rdd_entry,
        {
            "field_extraction": "field_extraction",
            "merge_state": "merge_state",
            "response_generation": "response_generation",
        },
    )

    # ── Shared data pipeline ──────────────────────────────────────────────────
    graph.add_edge("field_extraction", "merge_state")
    graph.add_conditional_edges(
        "merge_state",
        _route_after_merge,
        {"lease_lookup": "lease_lookup", "validation": "validation"},
    )
    graph.add_conditional_edges(
        "lease_lookup",
        _route_after_lease,
        {"validation": "validation", "response_generation": "response_generation"},
    )

    # ── Work Permit entry ─────────────────────────────────────────────────────
    graph.add_conditional_edges(
        "work_permit_entry",
        _route_after_work_permit_entry,
        {
            "field_extraction": "field_extraction",
            "merge_state": "merge_state",
            "response_generation": "response_generation",
        },
    )

    # ── Fitout Confirm entry ──────────────────────────────────────────────────
    graph.add_conditional_edges(
        "fitout_confirm_entry",
        _route_after_fitout_confirm_entry,
        {
            "field_extraction": "field_extraction",
            "merge_state": "merge_state",
            "response_generation": "response_generation",
        },
    )

    # ── General Inquiry entry ─────────────────────────────────────────────────
    graph.add_conditional_edges(
        "general_inquiry_entry",
        _route_after_general_inquiry_entry,
        {
            "field_extraction": "field_extraction",
            "merge_state": "merge_state",
            "response_generation": "response_generation",
        },
    )

    # ── Validation → confirmation ─────────────────────────────────────────────
    graph.add_conditional_edges(
        "validation",
        _route_after_validation,
        {
            "missing_field": "missing_field",
            "confirmation": "confirmation",
            "fm_confirmation": "fm_confirmation",
            "rdd_confirmation": "rdd_confirmation",
            "work_permit_confirmation": "work_permit_confirmation",
            "fitout_confirm_confirmation": "fitout_confirm_confirmation",
            "general_inquiry_confirmation": "general_inquiry_confirmation",
            "response_generation": "response_generation",
        },
    )
    graph.add_edge("missing_field", "response_generation")

    # ── Confirmation → payload builder ────────────────────────────────────────
    graph.add_conditional_edges(
        "confirmation",
        _route_after_confirmation,
        {"payload_builder": "payload_builder", "response_generation": "response_generation"},
    )
    graph.add_conditional_edges(
        "fm_confirmation",
        _route_after_fm_confirmation,
        {"fm_payload_builder": "fm_payload_builder", "response_generation": "response_generation"},
    )
    graph.add_conditional_edges(
        "rdd_confirmation",
        _route_after_rdd_confirmation,
        {
            "rdd_payload_builder": "rdd_payload_builder",
            "response_generation": "response_generation",
        },
    )
    graph.add_conditional_edges(
        "work_permit_confirmation",
        _route_after_work_permit_confirmation,
        {
            "work_permit_payload_builder": "work_permit_payload_builder",
            "response_generation": "response_generation",
        },
    )
    graph.add_conditional_edges(
        "fitout_confirm_confirmation",
        _route_after_fitout_confirm_confirmation,
        {
            "fitout_confirm_payload_builder": "fitout_confirm_payload_builder",
            "response_generation": "response_generation",
        },
    )
    graph.add_conditional_edges(
        "general_inquiry_confirmation",
        _route_after_general_inquiry_confirmation,
        {
            "general_inquiry_payload_builder": "general_inquiry_payload_builder",
            "response_generation": "response_generation",
        },
    )

    # ── Submission pipelines ──────────────────────────────────────────────────
    graph.add_edge("payload_builder", "api_submission")
    graph.add_edge("api_submission", "response_generation")
    graph.add_edge("fm_payload_builder", "fm_api_submission")
    graph.add_edge("fm_api_submission", "response_generation")
    graph.add_edge("rdd_payload_builder", "rdd_api_submission")
    graph.add_edge("rdd_api_submission", "response_generation")
    graph.add_edge("work_permit_payload_builder", "work_permit_api_submission")
    graph.add_edge("work_permit_api_submission", "response_generation")
    graph.add_edge("fitout_confirm_payload_builder", "fitout_confirm_api_submission")
    graph.add_edge("fitout_confirm_api_submission", "response_generation")
    graph.add_edge("general_inquiry_payload_builder", "general_inquiry_api_submission")
    graph.add_edge("general_inquiry_api_submission", "response_generation")

    # ── Sales Data agent edges ─────────────────────────────────────────────────
    graph.add_conditional_edges(
        "sales_data_entry",
        _route_after_sales_data_entry,
        {
            "csv_validation": "csv_validation",
            "csv_field_correction": "csv_field_correction",
            "manual_entry_extraction": "manual_entry_extraction",
            "tenant_status_lookup": "tenant_status_lookup",
            "admin_review_entry": "admin_review_entry",
            "sales_data_payload_builder": "sales_data_payload_builder",
            "sales_data_confirmation": "sales_data_confirmation",
            "manual_entry_confirmation": "manual_entry_confirmation",
            "supporting_doc_upload": "supporting_doc_upload",
            "supporting_doc_confirmation": "supporting_doc_confirmation",
            "supporting_doc_submission": "supporting_doc_submission",
            "pending_months_gate": "pending_months_gate",
            "stale_notification": "stale_notification",
            "response_generation": "response_generation",
        },
    )
    graph.add_edge("pending_months_gate", "response_generation")
    graph.add_conditional_edges(
        "csv_validation",
        _route_after_csv_validation,
        {
            "lease_validation": "lease_validation",
            "response_generation": "response_generation",
        },
    )
    graph.add_conditional_edges(
        "csv_field_correction",
        _route_after_csv_field_correction,
        {
            "lease_validation": "lease_validation",
            "response_generation": "response_generation",
        },
    )
    graph.add_conditional_edges(
        "lease_validation",
        _route_after_lease_validation,
        {
            "stale_notification": "stale_notification",
            "sales_data_confirmation": "sales_data_confirmation",
            "response_generation": "response_generation",
        },
    )
    graph.add_edge("stale_notification", "response_generation")
    graph.add_conditional_edges(
        "sales_data_confirmation",
        _route_after_sales_data_confirmation,
        {
            "sales_data_payload_builder": "sales_data_payload_builder",
            "response_generation": "response_generation",
        },
    )
    graph.add_conditional_edges(
        "sales_data_payload_builder",
        _route_after_sales_data_payload_builder,
        {
            "sales_data_api_submission": "sales_data_api_submission",
            "response_generation": "response_generation",
        },
    )
    graph.add_conditional_edges(
        "sales_data_api_submission",
        _route_after_sales_data_api_submission,
        {
            "submission_success": "submission_success",
            "submission_error": "submission_error",
            "response_generation": "response_generation",
        },
    )
    graph.add_edge("submission_success", "response_generation")
    graph.add_edge("submission_error", "response_generation")
    # Manual entry pipeline
    graph.add_conditional_edges(
        "manual_entry_extraction",
        _route_after_manual_entry_extraction,
        {
            "manual_entry_lease_validation": "manual_entry_lease_validation",
            "manual_entry_missing_field": "manual_entry_missing_field",
        },
    )
    graph.add_edge("manual_entry_missing_field", "response_generation")
    graph.add_conditional_edges(
        "manual_entry_lease_validation",
        _route_after_manual_entry_lease_validation,
        {
            "manual_entry_confirmation": "manual_entry_confirmation",
            "response_generation": "response_generation",
        },
    )
    graph.add_conditional_edges(
        "manual_entry_confirmation",
        _route_after_manual_entry_confirmation,
        {
            "manual_entry_submission": "manual_entry_submission",
            "response_generation": "response_generation",
        },
    )
    graph.add_conditional_edges(
        "manual_entry_submission",
        _route_after_manual_entry_submission,
        {
            "submission_success": "submission_success",
            "submission_error": "submission_error",
            "response_generation": "response_generation",
        },
    )
    # Status lookup and admin
    graph.add_edge("tenant_status_lookup", "response_generation")
    graph.add_conditional_edges(
        "admin_review_entry",
        _route_after_admin_review,
        {
            "admin_status_update": "admin_status_update",
            "response_generation": "response_generation",
        },
    )
    graph.add_edge("admin_status_update", "response_generation")
    graph.add_conditional_edges(
        "supporting_doc_upload",
        _route_after_supporting_doc_upload,
        {
            "supporting_doc_confirmation": "supporting_doc_confirmation",
            "response_generation": "response_generation",
        },
    )
    graph.add_conditional_edges(
        "supporting_doc_confirmation",
        _route_after_supporting_doc_confirmation,
        {
            "supporting_doc_submission": "supporting_doc_submission",
            "response_generation": "response_generation",
        },
    )
    graph.add_edge("supporting_doc_submission", "response_generation")

    # ── Turn finalisation — always runs ───────────────────────────────────────
    graph.add_edge("response_generation", "save_state")
    graph.add_edge("save_state", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_compiled_help_graph = None


def get_compiled_help_graph():
    """Return the lazily-compiled singleton Help Agent graph."""
    global _compiled_help_graph
    if _compiled_help_graph is None:
        _compiled_help_graph = build_help_agent_graph()
    return _compiled_help_graph


