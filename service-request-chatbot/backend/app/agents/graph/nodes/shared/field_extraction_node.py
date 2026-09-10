"""Field extraction node — calls FieldExtractionService and stores results in state.

Responsibilities
----------------
- Read ``user_message`` and ``workflow_stage`` from the graph state.
- Delegate extraction to ``FieldExtractionService`` (LLM call + retry logic).
- Emit a child LLM run span and an LLM call record via ``TraceManager`` so
  every extraction attempt is fully observable.
- Write ``extracted_fields`` to state as ``{field: {value, confidence}}``.

Non-responsibilities (enforced)
--------------------------------
- MUST NOT merge extracted fields into ``collected_data``.  That is the
  exclusive job of ``merge_state_node``.
- MUST NOT validate business rules or check required fields.
- MUST NOT perform lease / backend lookups.
"""

from __future__ import annotations

import re
import time
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.llm.gateway import get_default_gateway
from app.agents.registries.workflow_config import get_workflow_config
from app.agents.services.field_correction_service import (
    detect_correction_intent,
    is_unspecified_correction,
)
from app.agents.services.field_extraction_service import FieldExtractionService
from app.agents.services.validation_service import _SINGLE_DATE_FIELDS
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

# Vague relative-time words the extraction LLM is instructed to omit (see
# "other relative dates" in handover_extraction_prompt.py), but which it may
# not reliably comply with. Guarded deterministically here so a bare
# "tomorrow" never has a chance to reach an LLM call and get silently
# resolved into an absolute date the user never actually typed.
_RELATIVE_DATE_RE = re.compile(
    r"\b(today|tomorrow|tommorow|tmrw|yesterday|next week|next month|"
    r"day after tomorrow|this week|this month)\b",
    re.IGNORECASE,
)
# Any digit in the message means the user likely gave (or attempted) an
# actual date — don't block those even if a relative word also appears.
_HAS_DIGIT_RE = re.compile(r"\d")


def _is_bare_relative_date(message: str) -> bool:
    """True when *message* names a relative date term with no actual date."""
    return bool(_RELATIVE_DATE_RE.search(message)) and not _HAS_DIGIT_RE.search(message)


# Free-text fields whose value is simply whatever the user typed. When the
# answer contains the field's own name ("Post description", "Notes variant 2")
# the extraction LLM reads it as a reference to the field rather than content
# and returns an empty value — wiping the field and looping the question.
_FREE_TEXT_FIELDS: frozenset[str] = frozenset(
    {"title", "description", "notes", "comments"}
)

# Answers that genuinely mean "leave this blank".
_SKIP_ANSWER_RE = re.compile(
    r"^\s*(none|no|nope|skip|n/?a|nothing|no\s+\w+|not\s+now|-{1,2})\s*[.!]?\s*$",
    re.IGNORECASE,
)


def _has_usable_value(extracted: dict[str, Any]) -> bool:
    """True when any extracted field carries a real (non-empty) value.

    Extraction maps junk and refusals to an empty string rather than omitting
    the field, so ``{"endDate": {"value": ""}}`` means "nothing usable" just as
    much as ``{}`` does.
    """
    for entry in extracted.values():
        value = entry.get("value") if isinstance(entry, dict) else entry
        if value not in (None, "", []):
            return True
    return False


def _extract_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, AttributeError):
        return None


@trace_node("field_extraction", "AGENT")
async def field_extraction_node(state: ServiceRequestState) -> dict[str, Any]:
    """LangGraph node: extract handover fields from the user message.

    The ``@trace_node`` decorator handles the outer node-level run span
    (BEFORE_NODE / AFTER_NODE snapshots + SUCCESS/FAILED finish).  This
    function additionally opens a child ``LLM`` run span for the LLM call
    itself so token counts, latency, and retry metadata are captured
    separately from the node span.
    """
    user_message: str = state.get("user_message") or ""
    workflow_stage: str | None = state.get("workflow_stage")
    conversation_history: list[dict] = state.get("conversation_history") or []  # type: ignore[assignment]
    missing_fields: list[str] = state.get("missing_fields") or []  # type: ignore[assignment]
    active_field: str | None = state.get("active_field")
    collected_data: dict[str, Any] = state.get("collected_data") or {}  # type: ignore[assignment]

    # ── Deterministic "change field X" redirect detection ─────────────────
    # Runs before LLM extraction: the extractor is primed toward active_field
    # and the previous question, so it tends to mis-bind correction phrases
    # like "I want to change the title" to whatever field is being collected.
    correction = detect_correction_intent(
        user_message, collected_data, active_field, workflow_stage
    )
    if correction is not None:
        target_field, has_inline_value = correction
        if not has_inline_value:
            # Bare redirect with nothing to extract yet — skip the LLM call
            # entirely and let missing_field_node ask for the target field.
            log.info(
                "field_extraction.correction_redirect",
                target_field=target_field,
                active_field=active_field,
            )
            return {
                "extracted_fields": {},
                "active_field": target_field,
                "correction_pending_field": target_field,
            }
        # Inline value present ("change the title to Foo") — let the LLM
        # extract it, but point it at the target field first so it becomes
        # missing_fields[0] / "Field currently being collected" in the prompt.
        active_field = target_field

    # ── Unspecified correction request ─────────────────────────────────────
    # "can you fix it" / "i want to change something" names no field. Letting
    # it reach the LLM while a field is being collected stores the phrase as
    # that field's value, and can blank a field the user never mentioned.
    #
    # Never while a free-text field is being collected. Notes, comments,
    # descriptions and titles are prose, and prose about a handover routinely
    # carries a correction verb — "please update the flooring before
    # handover", "we need to change the shutters". No field is named in those,
    # so there is nothing to redirect to: the only effect is that the answer is
    # discarded and the same question is re-asked, and retyping the note fails
    # the identical way, so the conversation cannot move on. While one of these
    # fields is active the message IS the value, not an instruction.
    if active_field not in _FREE_TEXT_FIELDS and is_unspecified_correction(
        user_message, workflow_stage
    ):
        log.info(
            "field_extraction.correction_unspecified",
            active_field=active_field,
            user_message=user_message[:80],
        )
        unspec: dict[str, Any] = {"extracted_fields": {}, "active_field": active_field}
        if active_field and collected_data.get(active_field) not in (None, "", []):
            unspec["correction_pending_field"] = active_field
        return unspec

    # ── Deterministic relative-date guard ──────────────────────────────────
    # "tomorrow" / "today" must never satisfy a date field — always re-ask
    # for an explicit date rather than risk the LLM resolving it silently.
    if active_field in _SINGLE_DATE_FIELDS and _is_bare_relative_date(user_message):
        log.info(
            "field_extraction.relative_date_rejected",
            active_field=active_field,
            user_message=user_message,
        )
        patch: dict[str, Any] = {"extracted_fields": {}, "active_field": active_field}
        # When the field already holds a value it is not "missing", so
        # missing_field_node would advance to the next gap and the user's
        # correction would vanish without a word. Re-arm the correction so the
        # same field is asked again with an explicit format hint.
        if collected_data.get(active_field) not in (None, "", []):
            patch["correction_pending_field"] = active_field
        return patch

    # Prefer the field asked on the previous turn. ``missing_fields`` is cleared
    # at load time, so without ``active_field`` bare dates map to the wrong key.
    extraction_hint: list[str] | None = None
    if active_field:
        extraction_hint = [active_field]
        for field in missing_fields:
            if field != active_field:
                extraction_hint.append(field)
    elif missing_fields:
        extraction_hint = list(missing_fields)

    # Look up the agent's extraction prompt — falls back to handover default when absent.
    active_agent: str | None = state.get("active_agent")
    workflow_cfg = get_workflow_config(active_agent)
    extraction_prompt: str | None = workflow_cfg.extraction_prompt if workflow_cfg else None

    # Resolve trace plumbing injected by TraceManager / lifespan.
    tm = state.get("trace_manager")
    trace_id: UUID | None = _extract_uuid(state.get("trace_id"))

    # ── 1. Open child LLM-call run span ───────────────────────────────────
    # ``_trace_node_run_id`` is injected by the ``@trace_node`` decorator
    # so the LLM span is nested under the field_extraction node span.
    node_run_id: UUID | None = _extract_uuid(state.get("_trace_node_run_id"))
    llm_run_id: UUID | None = None
    if tm is not None and trace_id is not None:
        try:
            llm_run_id = await tm.start_run(
                trace_id=trace_id,
                run_name="field_extraction_llm_call",
                run_type="LLM",
                parent_run_id=node_run_id,
            )
        except Exception:
            log.warning("field_extraction.start_llm_run.failed", exc_info=True)

    # ── 2. Extract fields via service ──────────────────────────────────────
    gateway = get_default_gateway()
    service = FieldExtractionService(gateway=gateway)

    llm_wall_start = time.monotonic()
    result, meta = await service.extract(
        user_message,
        workflow_stage,
        recent_history=conversation_history[-4:] if conversation_history else None,
        missing_fields=extraction_hint,
        system_prompt=extraction_prompt,
    )

    # ── 3. Emit LLM call metadata ──────────────────────────────────────────
    if tm is not None and trace_id is not None and llm_run_id is not None:
        effective_latency = meta.latency_ms or int((time.monotonic() - llm_wall_start) * 1000)
        structured_output: dict[str, Any] = {
            "summary": result.summary,
            "fields": result.to_state_dict(),
            "retry_count": meta.retry_count,
            "parse_success": meta.parse_success,
        }
        try:
            await tm.capture_llm_call(
                trace_id=trace_id,
                run_id=llm_run_id,
                provider=getattr(gateway, "provider_name", "openai"),
                model=gateway.model,
                temperature=Decimal("0"),
                prompt_name="handover_extraction_prompt",
                prompt_version="1.0",
                input_tokens=meta.input_tokens or None,
                output_tokens=meta.output_tokens or None,
                total_tokens=(meta.input_tokens + meta.output_tokens) or None,
                latency_ms=effective_latency,
                structured_output=structured_output,
                parse_success=meta.parse_success,
                parse_error=meta.parse_error,
            )
        except Exception:
            log.warning("field_extraction.capture_llm_call.failed", exc_info=True)

        try:
            await tm.finish_run(
                run_id=llm_run_id,
                output=structured_output,
                status="SUCCESS" if meta.parse_success else "FAILED",
                error_message=meta.parse_error,
            )
        except Exception:
            log.warning("field_extraction.finish_llm_run.failed", exc_info=True)

    # ── 4. Handle empty / failed extraction ───────────────────────────────
    if not meta.parse_success:
        log.warning(
            "field_extraction.parse_failed",
            parse_error=meta.parse_error,
            retry_count=meta.retry_count,
        )
        result_update: dict[str, Any] = {"extracted_fields": {}}
        if correction is not None:
            result_update["active_field"] = active_field
        return result_update

    # ── 5. Build state update ──────────────────────────────────────────────
    # Store the full {value, confidence} shape so merge_state_node can apply
    # a confidence threshold at merge time rather than discarding information.
    extracted_fields = result.to_state_dict()

    log.info(
        "field_extraction.complete",
        fields=list(extracted_fields.keys()),
        retry_count=meta.retry_count,
    )

    # Deterministic rescue for free-text answers the LLM read as field
    # references rather than content. Verified live: "Post description" came
    # back as {"description": {"value": ""}} and "Post notes" as {}, which
    # blanked the field (auto-generating a replacement) and re-asked forever.
    # The user typed the value, so use it verbatim.
    if (
        active_field in _FREE_TEXT_FIELDS
        and not _SKIP_ANSWER_RE.match(user_message)
        and user_message.strip()
    ):
        entry = extracted_fields.get(active_field)
        current = entry.get("value") if isinstance(entry, dict) else entry
        if current in (None, "", []):
            log.info(
                "field_extraction.free_text_fallback",
                active_field=active_field,
                user_message=user_message[:80],
            )
            extracted_fields[active_field] = {
                "value": user_message.strip(),
                "confidence": 1.0,
            }

    final_update: dict[str, Any] = {"extracted_fields": extracted_fields}
    if correction is not None:
        final_update["active_field"] = active_field

    # Nothing usable came back while the bot was waiting on a field that
    # ALREADY holds a value — i.e. a correction re-ask answered with something
    # unparseable ("not a real date"). That field is not "missing", so the
    # normal flow would move on to the next gap and the user's correction
    # would vanish without a word; worse, their next message can then bind to
    # the wrong field. Re-arm the correction so the same field is asked again.
    if (
        not _has_usable_value(extracted_fields)
        and active_field
        and collected_data.get(active_field) not in (None, "", [])
        and active_field not in missing_fields
    ):
        log.info("field_extraction.correction_reasked", active_field=active_field)
        final_update["active_field"] = active_field
        final_update["correction_pending_field"] = active_field
    return final_update
