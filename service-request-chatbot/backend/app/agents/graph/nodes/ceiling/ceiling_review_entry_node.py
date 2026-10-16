"""Review entry for an Open / Close Ceiling Inspection (levels 1 and 2).

Reached from ``sr_status_sync`` when the live operation maps to
``CEILING_L1_REVIEW`` (DD_ENGINEER — the RDD Project Manager) or
``CEILING_L2_REVIEW`` (FM_MANAGER). The sub-category is read from the SR; the
reviewer never chooses it again.

Level 1 (PRD §5.3)
    upload the draft report for this sub-category → Approved / Approved with
    changes → a comment when "with changes" → confirmation card → PATCH.
Level 2 (PRD §5.4)
    Approve only when the draft report is on the SR → confirmation card → PATCH.
Both levels
    "Ask for more information" → comment → confirmation card → PATCH
    ``ACTION_REQUIRED``. Hidden at level 1 when the reviewer created the SR.
    Reject is never offered. Assign To is not built in this POC (the target is
    chosen at runtime on the platform; PRD open point 4).

The node is deterministic — no LLM. Review answers are held in
``collected_data["_ceiling_review"]`` so the platform payload that status sync
re-hydrates every turn cannot overwrite them.

Routing after this node
    ``confirmation_status == "CONFIRMED"`` → ``ceiling_review_submission``
    anything else → ``response_generation``
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.agents.graph.nodes.ceiling.ceiling_report import (
    APPROVED_RE,
    STATUS_ACTIONS,
    WITH_CHANGES_RE,
    document_requirement,
)
from app.agents.graph.nodes.fitout_confirm.fitout_confirm_entry_node import (
    _CONFIRM_PHRASES,
    _REJECT_PHRASES,
    _contains_phrase,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.ceiling_schema import (
    DOC_STATUS_APPROVED,
    DOC_STATUS_APPROVED_WITH_CHANGES,
    LEVEL_ROLE,
    LEVEL_ROLE_LABEL,
    REVIEW_ACTION_APPROVE,
    REVIEW_ACTION_MORE_INFO,
    STAGE_L1_REVIEW,
    STAGE_L2_REVIEW,
    has_draft_report,
    sub_category_label,
    user_roles,
)
from app.agents.services.review_decorations_service import build_view_sr_suggestions
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

REVIEW_KEY = "_ceiling_review"

_CONFIRM_ACTIONS: frozenset[str] = frozenset(
    {"confirm", "confirm_ceiling_review", "approve"}
)
_ACTION_MORE_INFO = "ceiling_more_info"
_ACTION_APPROVE = "ceiling_approve"
_ACTION_STATUS = STATUS_ACTIONS

_MORE_INFO_RE = re.compile(
    r"\b(more info(rmation)?|ask for (more )?info|need(s)? more|request (more )?info|send (it )?back)\b",
    re.IGNORECASE,
)
_WITH_CHANGES_RE = WITH_CHANGES_RE
_APPROVED_RE = APPROVED_RE

# Where the creator id may appear on the SR. The platform DTO does not
# guarantee one, so a missing id leaves "Ask for more information" available.
_CREATOR_KEYS: tuple[str, ...] = ("created_by", "CreatedBy", "createdBy", "user_id")


def _chip(label: str, action: str) -> dict[str, str]:
    return {"label": label, "value": label, "action": action}


def _suggestions(*options: dict[str, str]) -> dict[str, Any]:
    return {"suggestions": {"kind": "labeled_options", "options": list(options)}}


def _is_creator(state: ServiceRequestState) -> bool:
    user_id = str(state.get("user_id") or "").strip()
    if not user_id:
        return False
    inner = ((state.get("backend_refs") or {}).get("create_payload") or {}).get("payload") or {}
    for key in _CREATOR_KEYS:
        value = inner.get(key)
        if value not in (None, "") and str(value).strip() == user_id:
            return True
    return False


_document_requirement = document_requirement


def _can_review(stage: str, roles: set[str]) -> bool:
    """Only the live operation's role (or ADMIN) reviews.

    Unlike create, an unresolved role set is refused: the Mall Manager who
    raised the request, or any other viewer, must never get Approve.
    """
    return LEVEL_ROLE.get(stage, "") in roles or "ADMIN" in roles


def _wait(
    collected: dict[str, Any],
    review: dict[str, Any],
    message: str,
    decorations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    collected[REVIEW_KEY] = review
    return {
        "collected_data": collected,
        "status": "WAITING_FOR_USER",
        "confirmation_status": "PENDING" if review.get("pending_confirm") else None,
        "active_field": None,
        "response_message": message,
        "response_ui": {"type": "message", "message": message, "verbatim": True},
        "response_decorations": decorations or {},
    }


def _confirmation_card(
    stage: str,
    sub_category: str,
    sr_id: str,
    review: dict[str, Any],
    chips: tuple[dict[str, str], ...] = (),
) -> dict[str, Any]:
    label = sub_category_label(sub_category)
    action = review.get("action") or REVIEW_ACTION_APPROVE
    fields: list[dict[str, Any]] = [
        {"key": "sr_id", "label": "Service Request", "value": sr_id, "editable": False},
        {"key": "sub_category", "label": "Inspection", "value": label, "editable": False},
        {
            "key": "review_action",
            "label": "Action",
            "value": "Ask for more information" if action == REVIEW_ACTION_MORE_INFO else "Approve",
            "editable": False,
        },
    ]
    if action == REVIEW_ACTION_APPROVE and stage == STAGE_L1_REVIEW:
        fields.append(
            {
                "key": "document",
                "label": "Report",
                "value": f"{label} Report",
                "editable": False,
            }
        )
        fields.append(
            {
                "key": "document_status",
                "label": "Report Status",
                "value": (
                    "Approved with changes"
                    if review.get("document_status") == DOC_STATUS_APPROVED_WITH_CHANGES
                    else "Approved"
                ),
                "editable": False,
            }
        )
    if review.get("comment"):
        fields.append(
            {"key": "comment", "label": "Comment", "value": review["comment"], "editable": False}
        )
    card: dict[str, Any] = {
        "type": "confirmation_card",
        "requestType": label,
        "stage": stage,
        "fields": fields,
        "message": "Please review and confirm, or tell me what you'd like to change.",
        "confirmAction": "confirm_ceiling_review",
        "confirmLabel": "Confirm",
    }
    # The response finaliser never merges decoration chips into a card, so
    # the card carries its own ("Ask for more information").
    if chips:
        card["suggestions"] = {"kind": "labeled_options", "options": list(chips)}
    return card


def _read_turn(
    state: ServiceRequestState, stage: str, review: dict[str, Any]
) -> dict[str, Any]:
    """Fold this turn's chip or text into the held review answers."""
    action = str(state.get("action_override") or "")
    message = str(state.get("user_message") or "").strip()
    review = dict(review)

    if action == _ACTION_MORE_INFO or (not action and _MORE_INFO_RE.search(message)):
        if review.get("action") != REVIEW_ACTION_MORE_INFO:
            review.pop("comment", None)
        review["action"] = REVIEW_ACTION_MORE_INFO
        review.pop("awaiting", None)
        return review

    if review.get("awaiting") == "comment" and message and not action:
        review["comment"] = message
        review.pop("awaiting", None)
        return review

    if action in _ACTION_STATUS:
        review["action"] = REVIEW_ACTION_APPROVE
        review["document_status"] = _ACTION_STATUS[action]
        return review
    if action == _ACTION_APPROVE:
        review["action"] = REVIEW_ACTION_APPROVE
        return review

    if stage == STAGE_L1_REVIEW and message and _APPROVED_RE.search(message):
        review["action"] = REVIEW_ACTION_APPROVE
        review["document_status"] = (
            DOC_STATUS_APPROVED_WITH_CHANGES
            if _WITH_CHANGES_RE.search(message)
            else DOC_STATUS_APPROVED
        )
    return review


@trace_node("ceiling_review_entry", "AGENT")
async def ceiling_review_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Drive one turn of the level-1 or level-2 ceiling inspection review."""
    stage = str(state.get("workflow_stage") or "")
    sub_category = str(state.get("sub_category") or "")
    label = sub_category_label(sub_category)
    sr_id = str((state.get("backend_refs") or {}).get("sr_id") or "")
    collected = dict(state.get("collected_data") or {})

    # ── The live operation belongs to one role ────────────────────────────
    if not _can_review(stage, user_roles(state.get("auth_context"))):
        message = (
            f"{label} **{sr_id}** is with the {LEVEL_ROLE_LABEL.get(stage, 'reviewer')} "
            "for review. There is nothing for you to action on it right now."
        )
        return {
            "status": "WAITING_FOR_USER",
            "response_message": message,
            "response_ui": {"type": "message", "message": message, "verbatim": True},
            "response_decorations": {"suggestions": build_view_sr_suggestions(sr_id)},
        }

    review: dict[str, Any] = dict(collected.get(REVIEW_KEY) or {})
    if review.get("stage") != stage:
        review = {"stage": stage}

    # ── Reply to the confirmation card ────────────────────────────────────
    action = str(state.get("action_override") or "")
    user_message = str(state.get("user_message") or "")
    if review.get("pending_confirm"):
        if action in _CONFIRM_ACTIONS or (
            not action and _contains_phrase(user_message, _CONFIRM_PHRASES)
        ):
            review.pop("pending_confirm", None)
            collected[REVIEW_KEY] = review
            log.info("ceiling_review_entry.confirmed", stage=stage, action=review.get("action"))
            return {
                "collected_data": collected,
                "confirmation_status": "CONFIRMED",
                "status": "IN_PROGRESS",
            }
        if action == "cancel" or (
            not action
            and _contains_phrase(user_message, _REJECT_PHRASES)
            and not _MORE_INFO_RE.search(user_message)
        ):
            review = {"stage": stage}
            prefix = "OK — nothing was sent. "
        else:
            review.pop("pending_confirm", None)
            prefix = ""
    else:
        prefix = ""

    review = _read_turn(state, stage, review)

    more_info_allowed = not (stage == STAGE_L1_REVIEW and _is_creator(state))
    if review.get("action") == REVIEW_ACTION_MORE_INFO and not more_info_allowed:
        review.pop("action", None)
        prefix += "You raised this request, so you can't ask yourself for more information. "

    more_info_chip = (_chip("Ask for more information", _ACTION_MORE_INFO),) if more_info_allowed else ()
    has_draft = has_draft_report(state.get("documents"), sub_category)

    # ── Ask for more information ──────────────────────────────────────────
    if review.get("action") == REVIEW_ACTION_MORE_INFO:
        if not review.get("comment"):
            review["awaiting"] = "comment"
            return _wait(
                collected,
                review,
                prefix + "What information do you need from the requester?",
            )
        review["pending_confirm"] = True
        result = _wait(collected, review, prefix + "Please confirm the request for more information.")
        result["response_ui"] = _confirmation_card(stage, sub_category, sr_id, review)
        result["confirmation_required"] = True
        return result

    # ── Level 1: report → status → comment → confirm ─────────────────────
    if stage == STAGE_L1_REVIEW:
        if not has_draft:
            deco = {"document_requirement": _document_requirement(state, sub_category)}
            deco.update(_suggestions(*more_info_chip) if more_info_chip else {})
            return _wait(
                collected,
                review,
                prefix + f"Upload the {label} report to continue.",
                deco,
            )
        if not review.get("document_status"):
            # The upload card stays the turn's UI, as on the handover reviews:
            # the turn after an upload is a silent form sync, and the client
            # only shows a silent reply when it is a card.
            deco = {"document_requirement": _document_requirement(state, sub_category)}
            deco.update(
                _suggestions(
                    _chip("Approved", "ceiling_status_approved"),
                    _chip("Approved with changes", "ceiling_status_approved_with_changes"),
                    *more_info_chip,
                )
            )
            return _wait(
                collected,
                review,
                prefix + f"The {label} report is uploaded. Is it approved, or approved with changes?",
                deco,
            )
        review["action"] = REVIEW_ACTION_APPROVE
        if (
            review.get("document_status") == DOC_STATUS_APPROVED_WITH_CHANGES
            and not review.get("comment")
        ):
            review["awaiting"] = "comment"
            return _wait(collected, review, prefix + "Please add a comment describing the changes.")

    # ── Level 2: approve only once the draft report is on file ───────────
    if stage == STAGE_L2_REVIEW:
        if not has_draft:
            return _wait(
                collected,
                review,
                prefix
                + "This request cannot be approved yet. The RDD Project Manager still "
                f"needs to upload the {label} report.",
                _suggestions(*more_info_chip) if more_info_chip else None,
            )
        review["action"] = REVIEW_ACTION_APPROVE

    if stage not in (STAGE_L1_REVIEW, STAGE_L2_REVIEW):
        log.warning("ceiling_review_entry.unexpected_stage", stage=stage)
        return {}

    review["pending_confirm"] = True
    opening = (
        f"The {label} report is on file. Confirm approval."
        if stage == STAGE_L2_REVIEW
        else "Please confirm your review."
    )
    result = _wait(collected, review, prefix + opening, _suggestions(*more_info_chip) if more_info_chip else None)
    result["response_ui"] = _confirmation_card(stage, sub_category, sr_id, review, more_info_chip)
    result["confirmation_required"] = True
    return result
