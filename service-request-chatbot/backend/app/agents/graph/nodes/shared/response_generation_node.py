"""LLM-driven response generation node.

Every graph path converges here before save_state → END.

Responsibilities
----------------
- Call the LLM with full workflow context to produce a natural, personalised
  assistant message.
- Preserve structured ``response_ui`` payloads set by prior nodes unchanged
  (confirmation cards, lease selection lists, etc.); only the text ``message``
  field inside them is overwritten with the LLM output.
- Fall back gracefully to the raw ``response_message`` hint when the LLM call
  fails so the turn always completes.
- Force ``status = "WAITING_FOR_USER"`` for all non-terminal paths.
- Keep terminal ``SUBMITTED`` / ``FAILED`` / ``COMPLETED`` messages verbatim
  (no LLM rewrite / repeat guard) so success text is never replaced by the
  generic processing placeholder.

Non-responsibilities
--------------------
- Does not route or make workflow decisions.
- Does not modify ``collected_data`` or any domain fields.
- Does not submit or approve service requests.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from typing import Any

from app.agents.graph.nodes.work_permit.work_permit_review_context import (
    build_wp_review_context,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.llm.gateway import get_default_gateway
from app.agents.prompts.response_generation_prompt import (
    RESPONSE_GENERATION_SYSTEM_PROMPT,
    build_document_refs,
    build_response_generation_context,
    document_file_names,
)
from app.agents.services.work_permit_review_summary import (
    WpDetailRow,
    build_wp_review_details,
)
from app.observability.decorators import trace_node
from app.observability.llm_capture import capture_complete_json

logger = logging.getLogger(__name__)

_FALLBACK_RESPONSE = (
    "I'm processing your request. Please wait a moment or provide more details."
)

# Terminal statuses whose ``response_message`` must not be overwritten.
_TERMINAL_STATUSES = {"SUBMITTED", "FAILED", "COMPLETED"}

# A generated reply this similar to the previous assistant turn is a repeat of
# the question the user just answered, not a new one.
_REPEAT_SIMILARITY_THRESHOLD = 0.75


def _last_assistant_message(conversation_history: list[dict]) -> str:
    for turn in reversed(conversation_history or []):
        if turn.get("role") == "assistant" and turn.get("content"):
            return str(turn["content"])
    return ""


def _mentions_field(message: str, asking_field: str | None) -> bool:
    """True when *message* plausibly asks about *asking_field*.

    Consecutive questions in this workflow are near-identical by design
    ("…additional **notes**…" vs "…additional **comments**…" score 0.96
    similarity), so raw similarity alone rejects perfectly good questions.
    Checking that the reply actually names the target field distinguishes
    "asked the next field in a familiar phrasing" (fine) from "re-asked the
    previous field" (the bug).
    """
    if not asking_field:
        return False
    from app.agents.prompts.response_generation_prompt import FIELD_LABELS

    label = FIELD_LABELS.get(asking_field, asking_field)
    # Split camelCase (endDate → end Date) before tokenising.
    label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label)
    tokens = [t for t in re.split(r"[^a-z]+", label.lower()) if len(t) > 3]
    if not tokens:
        return False
    lowered = message.lower()
    return all(t in lowered for t in tokens)


def _is_repeat_of_previous_question(message: str, previous: str) -> bool:
    """True when *message* just re-asks the previous assistant question.

    Observed live: after the user answered a field-correction question, the
    LLM ignored the "ask for X" constraint and re-emitted the previous turn's
    question verbatim-ish — quoting the now-stale value. The backend state was
    correct, so only the user-facing text was wrong, which reads as the bot
    looping and losing the answer.
    """
    if not message or not previous:
        return False
    ratio = difflib.SequenceMatcher(
        None, " ".join(message.lower().split()), " ".join(previous.lower().split())
    ).ratio()
    return ratio >= _REPEAT_SIMILARITY_THRESHOLD


_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_DOC_REF = re.compile(r"\bdoc_\d+\b")


def _card_text(value: Any) -> str:
    """Plain card text, or "" when it is missing or carries an id/handle."""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    text = str(value).strip()
    return "" if _UUID.search(text) or _DOC_REF.search(text) else text


def _details_card(
    raw: Any,
    doc_refs: dict[str, str],
    file_names: dict[str, str],
) -> dict[str, Any] | None:
    """The model's optional ``card`` as a ``details_card``, or None when unusable.

    Values the model wrote are kept only as plain text; files resolve from the
    ``doc_N`` handles it was shown back to real document ids it never saw.
    """
    if not isinstance(raw, dict):
        return None
    fields = [
        {"label": label, "value": value}
        for item in raw.get("fields") or []
        if isinstance(item, dict)
        and (label := _card_text(item.get("label")))
        and (value := _card_text(item.get("value")))
    ]
    ids_by_ref = {ref: doc_id for doc_id, ref in doc_refs.items()}
    files: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw.get("files") or []:
        if not isinstance(item, dict):
            continue
        doc_id = ids_by_ref.get(str(item.get("ref") or "").strip())
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        file_name = file_names.get(doc_id) or "Document"
        files.append(
            {
                "label": _card_text(item.get("label")) or file_name,
                "fileName": file_name,
                "documentId": doc_id,
            }
        )
    if not fields and not files:
        return None
    card: dict[str, Any] = {"type": "details_card", "fields": fields}
    title = _card_text(raw.get("title"))
    if title:
        card["title"] = title
    if files:
        card["files"] = files
    return card


def _wp_readable_rows(
    state: ServiceRequestState,
) -> tuple[list[WpDetailRow] | None, str | None]:
    """Work Permit rows as people read them (no raw codes), plus the request type.

    Submitted requests only: a draft's create prompts rely on the raw fields
    (e.g. confirming a just-collected contact number the rows omit).
    """
    if state.get("active_agent") != "work_permit_agent":
        return None, None
    if not (state.get("backend_refs") or {}).get("sr_id"):
        return None, None
    collected: dict[str, Any] = state.get("collected_data") or {}
    if not collected:
        return None, None
    ctx = build_wp_review_context(
        state, workflow_stage=str(state.get("workflow_stage") or "CREATE_WORK_PERMIT")
    )
    rows = build_wp_review_details(collected, subtype=ctx.subtype)
    permit = next((r["value"] for r in rows if r.get("key") == "work_permit_type"), "")
    return rows, f"Work Permit — {permit}" if permit else "Work Permit"


_SALES_REPEAT_FALLBACK = (
    "I'm not sure what you would like to do next. You can continue with the "
    "current step, or say **upload sales data**, **check my status**, "
    "**help** or **cancel**."
)


@trace_node("response_generation", "AGENT")
async def response_generation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Generate a natural LLM response and mark the turn as WAITING_FOR_USER."""

    current_message: str | None = state.get("response_message")
    current_status: str | None = state.get("status")
    current_ui: dict[str, Any] | None = state.get("response_ui")

    # Terminal paths: submission success/failure — keep the authored message.
    # An LLM rewrite + repeat guard was discarding WP create success text and
    # falling through to the generic "I'm processing…" placeholder.
    is_terminal = current_status in _TERMINAL_STATUSES
    if is_terminal and current_message:
        updates: dict[str, Any] = {"response_message": current_message}
        if current_ui:
            updates["response_ui"] = {**current_ui, "message": current_message}
        else:
            updates["response_ui"] = {
                "type": "message",
                "message": current_message,
            }
        return updates

    # Sales Data Agent replies are authored in one professional voice in the
    # nodes themselves; an LLM rewrite reintroduced "Good news!"-style
    # variance on any reply that forgot the per-message verbatim flag.
    if current_message and state.get("active_agent") == "sales_data_agent":
        # Never answer two different messages with the identical reply — that
        # reads as the assistant being stuck. Offer the ways forward instead.
        previous = next(
            (
                str(m.get("content") or "")
                for m in reversed(state.get("conversation_history") or [])
                if isinstance(m, dict) and m.get("role") == "assistant"
            ),
            "",
        )
        # A file upload or button click is a new answer the user gave, not
        # a typed message the agent failed to understand — re-uploading the
        # same broken file must get the same fix-and-re-upload reply.
        acted = bool(
            state.get("action_override")
            or any(
                (state.get("corrected_fields") or {}).get(key)
                for key in ("csv_session_id", "doc_session_id")
            )
        )
        if (
            not acted
            and previous.strip() == current_message.strip()
            and (state.get("user_message") or "").strip()
        ):
            # Replace only the text — the card under it (attach control,
            # review table, confirm buttons) must stay so the user can act.
            current_message = _SALES_REPEAT_FALLBACK
            current_ui = (
                {**current_ui, "message": current_message}
                if current_ui
                else {"type": "message", "verbatim": True, "message": current_message}
            )
        updates = {"response_message": current_message}
        if current_ui:
            updates["response_ui"] = {**current_ui, "message": current_ui.get("message") or current_message}
        if not is_terminal:
            updates["status"] = "WAITING_FOR_USER"
        return updates

    # Fitout original-date confirm questions must keep the concrete date.
    # An LLM rewrite was dropping it and falling back to a generic ask.
    if current_ui and current_ui.get("verbatim") and current_message:
        updates: dict[str, Any] = {
            "response_message": current_message,
            "response_ui": {**current_ui, "message": current_message},
        }
        if not is_terminal:
            updates["status"] = "WAITING_FOR_USER"
        return updates

    # A confirmation card authors its own text and deliberately leaves
    # response_message None (see the four *_confirmation_node modules), so there
    # is no hint for the LLM to rewrite — it improvises from conversation
    # history instead. Observed on an RDD review card as "The fitout end date is
    # currently set to 24/08/2026. Please confirm the new fitout end date you'd
    # like to set.": the user had just changed that date to 25/08, merge_state
    # had stored it, and the card underneath showed the new value — only the
    # sentence above it quoted the replaced one and re-asked a question already
    # answered. The card is the contract at this point; keep its wording.
    if (
        current_ui
        and not current_message
        and current_ui.get("type") == "confirmation_card"
        and current_ui.get("message")
    ):
        card_message = str(current_ui["message"])
        updates = {"response_message": card_message, "response_ui": current_ui}
        if not is_terminal:
            updates["status"] = "WAITING_FOR_USER"
        return updates

    # ── Build context for the LLM ───────────────────────────────────────────
    collected_data: dict[str, Any] = state.get("collected_data") or {}
    missing_fields: list[str] = state.get("missing_fields") or []
    validation_errors: list[dict] = state.get("validation_errors") or []
    conversation_history: list[dict] = state.get("conversation_history") or []  # type: ignore[typeddict-item]
    user_message: str = state.get("user_message") or ""
    documents: list[dict] = state.get("documents") or []  # type: ignore[assignment]
    doc_refs = build_document_refs(documents, collected_data)

    # Nodes that emit a UI payload without a response_message (e.g. the
    # pre-submission upload gate) still carry user-facing text inside that
    # payload. Falling straight through to the generic placeholder left the
    # LLM with nothing to say, so it improvised from conversation history and
    # re-asked the question the user had just answered.
    response_intent = (
        current_message
        or (current_ui or {}).get("message")
        or _FALLBACK_RESPONSE
    )

    readable_rows, request_type = _wp_readable_rows(state)
    user_content = build_response_generation_context(
        readable_rows=readable_rows,
        request_type=request_type,
        user_message=user_message,
        response_intent=response_intent,
        workflow_stage=state.get("workflow_stage"),
        intent=state.get("intent"),
        collected_data=collected_data,
        missing_fields=missing_fields,
        validation_errors=validation_errors,
        confirmation_status=state.get("confirmation_status"),
        response_ui_type=current_ui.get("type") if current_ui else None,
        conversation_history=conversation_history,
        asking_field=current_ui.get("field") if current_ui else None,
        doc_refs=doc_refs,
        documents=documents,
    )

    # ── Call the LLM ────────────────────────────────────────────────────────
    llm_message: str | None = None
    llm_card: dict[str, Any] | None = None
    in_tokens = out_tokens = llm_ms = 0
    gateway = get_default_gateway()
    try:
        parsed, in_tokens, out_tokens, llm_ms = await gateway.complete_json(
            system_prompt=RESPONSE_GENERATION_SYSTEM_PROMPT,
            user_message=user_content,
        )
        llm_message = parsed.get("message") or None
        # A card only stands in for plain text; a card a node already planned
        # (confirmation, review, question) is the contract for this turn.
        if not current_ui or current_ui.get("type") == "message":
            llm_card = _details_card(
                parsed.get("card"), doc_refs, document_file_names(documents)
            )
        await capture_complete_json(
            state,
            run_name="response_generation_llm_call",
            prompt_name="response_generation_prompt",
            gateway=gateway,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            latency_ms=llm_ms,
            structured_output={
                "message": (llm_message or "")[:500],
                "card": (
                    {
                        "title": llm_card.get("title"),
                        "fields": len(llm_card["fields"]),
                        "files": len(llm_card.get("files") or []),
                    }
                    if llm_card
                    else None
                ),
            },
            parse_success=bool(llm_message),
        )
        if llm_message:
            logger.debug("response_generation_node.llm_success: %s", llm_message[:80])
        else:
            logger.warning("response_generation_node.llm_empty_message: %s", parsed)
    except json.JSONDecodeError as exc:
        logger.warning("response_generation_node.llm_json_parse_failed: %s", exc)
        await capture_complete_json(
            state,
            run_name="response_generation_llm_call",
            prompt_name="response_generation_prompt",
            gateway=gateway,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            parse_success=False,
            parse_error=str(exc),
        )
    except Exception as exc:
        logger.warning("response_generation_node.llm_call_failed: %s", exc)
        await capture_complete_json(
            state,
            run_name="response_generation_llm_call",
            prompt_name="response_generation_prompt",
            gateway=gateway,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            latency_ms=llm_ms,
            parse_success=False,
            parse_error=str(exc),
        )

    # ── Guard: reject a reply that re-asks the previous question ────────────
    # The prompt states the target field twice (top and, most importantly,
    # after the conversation history), but the model can still latch onto the
    # last assistant turn. When it does, prefer a plain deterministic ask for
    # the field the backend is actually collecting over a message that
    # contradicts it — a slightly blunter sentence is far better than looping
    # the user on a question they already answered.
    asking_field: str | None = current_ui.get("field") if current_ui else None
    if (
        llm_message
        # A reply that names the field being collected is legitimate even when
        # it closely resembles the previous question — sibling fields share
        # phrasing. Only unrecognised replies are candidates for rejection.
        and not _mentions_field(llm_message, asking_field)
        and _is_repeat_of_previous_question(
            llm_message, _last_assistant_message(conversation_history)
        )
    ):
        from app.agents.prompts.response_generation_prompt import FIELD_LABELS

        logger.warning(
            "response_generation_node.repeated_previous_question: "
            "asking_field=%s discarded=%r",
            asking_field,
            llm_message[:120],
        )
        if asking_field:
            # The collecting node already wrote a proper question for this
            # field — chips and all — and it is sitting in the UI payload.
            # Prefer it over a sentence assembled from the field name, which
            # reached users as "Please provide the fitout complete done."
            node_question = (current_ui or {}).get("message")
            if node_question:
                llm_message = str(node_question)
            else:
                label = FIELD_LABELS.get(asking_field, asking_field.replace("_", " "))
                llm_message = (
                    f"Thanks — that's been updated. Please provide the {label}."
                )
        else:
            # No field is being collected (e.g. the pre-submission upload gate,
            # which leaves response_message None). The turn has nothing left to
            # ask, so re-asking the previous question is pure noise — defer to
            # whatever text the UI payload carries.
            llm_message = (current_ui or {}).get("message") or None

    # Fall back to the raw hint from prior nodes if LLM failed or returned nothing.
    final_message = llm_message or response_intent or _FALLBACK_RESPONSE

    # ── Merge LLM message into structured UI when present ───────────────────
    updated_ui: dict[str, Any] | None = None
    if llm_card:
        updated_ui = {**llm_card, "message": final_message}
    elif current_ui:
        updated_ui = {**current_ui, "message": final_message}

    # ── Build output ────────────────────────────────────────────────────────
    updates: dict[str, Any] = {"response_message": final_message}

    if updated_ui is not None:
        updates["response_ui"] = updated_ui

    # Preserve terminal statuses; set WAITING_FOR_USER for all other paths.
    if not is_terminal:
        updates["status"] = "WAITING_FOR_USER"

    return updates
