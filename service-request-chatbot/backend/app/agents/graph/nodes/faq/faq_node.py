"""FAQ node — answer platform Q&A questions via hybrid RAG search + embedded prompt.

Responsibilities
----------------
- When the supervisor classifies intent as ``ASK_HELP`` or ``UNKNOWN``, this
  node handles the response by:
    1. Attempting a hybrid Azure AI Search query to retrieve relevant knowledge
       base chunks (``cenomi-help-index``).
    2. Prepending the retrieved context block to the user message before calling
       the LLM with the FAQ system prompt.
- Falls back gracefully to the static ``FAQ_SYSTEM_PROMPT`` if:
    - No search credentials are configured (``get_search_repository()`` → ``None``)
    - Any exception occurs during embed, search, or formatting

Citations are NOT surfaced.  Retrieved chunks ground the answer but their
titles are never shown to the user: no inline ``[Source: ...]`` markers, and
``faq_sources`` is always returned empty so the MSP ``sources`` array, the SSE
``source`` events, and the ``/api/chat/service-request`` ``faq_sources`` field
all stay empty.  Retrieved titles are logged for RAG-quality debugging.

Non-responsibilities
--------------------
- MUST NOT collect, validate, or process form fields.
- MUST NOT submit or approve service requests.
- Does not create a session draft — Q&A turns are stateless.
"""

from __future__ import annotations

import json
import re
import time
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.llm.gateway import get_default_gateway
from app.agents.prompts.faq_prompt import FAQ_SYSTEM_PROMPT
from app.agents.services.context_builder import build_agent_context, format_context_for_prompt
from app.integrations.factory import get_search_repository
from app.integrations.search.base import SearchResult
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

_FALLBACK_MESSAGE = (
    "I'm here to help with questions about the platform and service requests. "
    "Could you rephrase your question?"
)

# Arabic Unicode block — used for language detection
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _detect_language(text: str) -> str:
    """Return ``"ar"`` if the text contains Arabic characters, else ``"en"``."""
    return "ar" if _ARABIC_RE.search(text) else "en"


def _format_rag_context(results: list[SearchResult]) -> tuple[str, list[dict]]:
    """Format search results into a context block and a citations list.

    Returns
    -------
    rag_block:
        Markdown-formatted string prepended to the user message:
        ``### Relevant Knowledge Base Results\\n\\n...``
    faq_sources:
        List of ``{source_type, title}`` dicts for the API response / state.
    """
    if not results:
        return "", []

    lines: list[str] = ["### Relevant Knowledge Base Results\n"]
    sources: list[dict] = []

    for idx, r in enumerate(results, start=1):
        lines.append(f"**[{idx}] {r.title}** (type: {r.source_type})")
        if r.mall_name:
            lines.append(f"Mall: {r.mall_name}")
        lines.append(r.content.strip())
        lines.append("")
        sources.append({"source_type": r.source_type, "title": r.title})

    return "\n".join(lines), sources


def _render_structured(value: Any, depth: int = 0) -> str:
    """Render a non-string ``message`` value as readable markdown.

    The LLM is asked for ``{"message": "<string>"}``, but on "give me all the
    details of X" queries it sometimes answers with the knowledge-base chunk's
    own structure instead — ``{"message": {"Mall Name": ..., "Floors": ...}}``.
    The old code did ``str(parsed["message"])``, which stringifies a dict to its
    Python repr and shipped ``{'Mall Name': 'Mall Of Arabia', ...}`` — single
    quotes and all — straight to the user.  Coerce it to markdown instead so a
    stray structured answer still reads as prose.
    """
    indent = "  " * depth

    if isinstance(value, dict):
        lines: list[str] = []
        for key, val in value.items():
            label = str(key).replace("_", " ").strip()
            if isinstance(val, (dict, list)):
                lines.append(f"{indent}- **{label}:**")
                lines.append(_render_structured(val, depth + 1))
            else:
                lines.append(f"{indent}- **{label}:** {val}")
        return "\n".join(lines)

    if isinstance(value, list):
        # A list of scalars reads better inline than as one bullet per item.
        if all(not isinstance(v, (dict, list)) for v in value):
            return f"{indent}{', '.join(str(v) for v in value)}"
        return "\n".join(_render_structured(v, depth) for v in value)

    return f"{indent}{value}"


async def _mentions_a_mall(message: str, search_repo: Any) -> bool:
    """True when the question already names an indexed mall.

    An explicit mall must win over page context, in retrieval as well as in
    the answer — so the caller skips query biasing when this returns True.
    """
    lister = getattr(search_repo, "list_mall_names", None)
    if lister is None:
        return False
    try:
        names = await lister()
    except Exception:
        return False
    lowered = message.lower()
    return any(n.lower() in lowered for n in names)


def _context_mall_name(state: ServiceRequestState) -> str:
    """Mall name supplied directly by the widget, if any."""
    msp_context: dict = state.get("msp_context") or {}  # type: ignore[assignment]
    return str(msp_context.get("mall_name") or "").strip()


async def _resolve_active_mall(state: ServiceRequestState, search_repo: Any) -> str:
    """Mall the user is currently looking at, from the widget's page context.

    Without this, "who is the mall manager" is unanswerable — there are 20
    malls and nothing in the question says which.  Two ways in, in order:

    1. ``mall_name`` sent by the widget — free, no lookup.
    2. ``property_id`` from the page URL (/propertydetails?propertyId=3691),
       resolved against the index. The frontend always has the id, so this
       path works even when it cannot supply the name.
    """
    name = _context_mall_name(state)
    if name:
        return name

    msp_context: dict = state.get("msp_context") or {}  # type: ignore[assignment]
    raw_id = msp_context.get("property_id")
    if raw_id in (None, "") or search_repo is None:
        return ""
    try:
        property_id = int(raw_id)
    except (TypeError, ValueError):
        return ""

    lookup = getattr(search_repo, "lookup_mall_name", None)
    if lookup is None:
        return ""
    try:
        return (await lookup(property_id)) or ""
    except Exception as exc:
        log.warning("faq_node.mall_lookup_failed", error=str(exc))
        return ""


def _build_faq_user_content(state: ServiceRequestState, rag_block: str) -> str:
    """Build the user-facing prompt content for the FAQ node.

    Prepends the RAG context block (if any) before the user question and the
    last 4 conversation turns so the model answers follow-ups accurately.
    """
    message = state.get("user_message") or ""
    parts: list[str] = []

    if rag_block:
        parts.append(rag_block)

    # Page context: resolves "the mall" / "this mall" to the one on screen.
    # An explicitly named mall in the question still takes precedence.
    active_mall = str(state.get("_active_mall") or "") or _context_mall_name(state)
    if active_mall:
        parts.append(
            f"Page context: the user is currently viewing **{active_mall}**. "
            f"If the question refers to 'the mall', 'this mall', or names no "
            f"mall at all, it is about {active_mall}. If the question names a "
            f"different mall, answer about the mall they named instead."
        )

    parts.append(f"User question: {message}")

    context = build_agent_context(state)
    if context.get("viewing_notification_id"):
        parts.append(f"Viewing notification ID: {context['viewing_notification_id']}")
    if context.get("viewing_service_request_id"):
        parts.append(f"Viewing service request ID: {context['viewing_service_request_id']}")
    snapshot = context.get("viewing_notification") or {}
    if snapshot.get("title") or snapshot.get("body"):
        parts.append(format_context_for_prompt({"viewing_notification": snapshot}))

    history: list[dict] = state.get("conversation_history") or []  # type: ignore[assignment]
    recent = history[-4:]
    if recent:
        parts.append("Recent conversation (oldest first):")
        for turn in recent:
            role = (turn.get("role") or "unknown").capitalize()
            content = (turn.get("content") or "").strip()
            if content:
                parts.append(f"  {role}: {content}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


@trace_node("faq", "AGENT")
async def faq_node(state: ServiceRequestState) -> dict[str, Any]:
    """LangGraph node: answer a platform FAQ question via RAG + LLM.

    Attempts hybrid Azure AI Search retrieval, falls back to the static
    ``FAQ_SYSTEM_PROMPT`` on any error.  Always returns ``faq_sources`` empty —
    retrieval grounds the answer, but citations are not shown to the user.
    """
    user_message: str = state.get("user_message") or ""
    gateway = get_default_gateway()

    rag_block = ""
    retrieved_sources: list[dict] = []

    search_repo = get_search_repository()

    if search_repo is not None:
        try:
            # Prefer language explicitly set by the MSP adapter layer; fall back to detection.
            lang = state.get("language") or _detect_language(user_message)

            # Extract optional URL filter from MSP page-context hint.
            msp_context: dict = state.get("msp_context") or {}  # type: ignore[assignment]
            url_pattern: str | None = msp_context.get("current_url_pattern")

            t_search = time.monotonic()
            # Pass url_pattern as a keyword argument; SearchRepository
            # implementations that do not support it will silently ignore it
            # because the Protocol does not declare it as a required param.
            # Bias retrieval toward the mall being viewed — but ONLY when the
            # user has not named a mall themselves. Layering "(Yasmeen Mall)"
            # onto "who is the mall manager of Tala Mall" makes a query that
            # matches neither well, and buries the card the user asked for.
            active_mall = await _resolve_active_mall(state, search_repo)
            state["_active_mall"] = active_mall  # reused when building the prompt
            search_text = user_message
            if active_mall and not await _mentions_a_mall(user_message, search_repo):
                search_text = f"{user_message} ({active_mall})"
            search_kwargs: dict = {"query": search_text, "lang": lang}
            if url_pattern:
                search_kwargs["url_filter"] = url_pattern
            try:
                results = await search_repo.search(**search_kwargs)
            except TypeError:
                # Fallback: implementation doesn't accept url_filter — retry without it.
                results = await search_repo.search(query=search_text, lang=lang)
            search_total_ms = int((time.monotonic() - t_search) * 1000)

            log.info(
                "faq_node.search_complete",
                latency_ms=search_total_ms,
                hits=len(results),
                lang=lang,
            )

            rag_block, retrieved_sources = _format_rag_context(results)

        except Exception as exc:
            log.warning(
                "faq_node.rag_fallback",
                reason="search_error",
                error=str(exc),
            )
            rag_block = ""
            retrieved_sources = []
    else:
        log.debug("faq_node.rag_fallback", reason="no_repo")

    user_content = _build_faq_user_content(state, rag_block)
    answer: str = _FALLBACK_MESSAGE

    parsed: Any = None
    in_tokens = out_tokens = llm_ms = 0
    parse_error: str | None = None
    try:
        parsed, in_tokens, out_tokens, llm_ms = await gateway.complete_json(
            system_prompt=FAQ_SYSTEM_PROMPT,
            user_message=user_content,
        )
        if isinstance(parsed, dict) and parsed.get("message"):
            raw_message = parsed["message"]
            if isinstance(raw_message, str):
                answer = raw_message.strip()
            else:
                # Model ignored the string contract and nested structured data.
                log.warning(
                    "faq_node.structured_message_coerced",
                    message_type=type(raw_message).__name__,
                )
                answer = _render_structured(raw_message).strip() or _FALLBACK_MESSAGE
        else:
            log.warning("faq_node.unexpected_llm_shape", parsed=parsed)
    except json.JSONDecodeError as exc:
        parse_error = f"JSONDecodeError: {exc}"
        log.warning("faq_node.json_parse_error", error=str(exc))
    except Exception as exc:
        parse_error = str(exc)
        log.exception("faq_node.llm_failed", error=str(exc))

    await _capture_faq_llm_call(
        state,
        gateway=gateway,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        latency_ms=llm_ms,
        parse_success=parse_error is None,
        parse_error=parse_error,
    )

    log.info(
        "faq_node.answered",
        answer_length=len(answer),
        rag_used=bool(rag_block),
        retrieved_count=len(retrieved_sources),
    )

    # Citations are deliberately not surfaced.  ``faq_sources`` is the single
    # producer for the MSP ``sources`` array, the SSE ``source`` events, and the
    # ``/api/chat/service-request`` ``faq_sources`` field — returning an empty
    # list here suppresses the source chips on every surface at once.  Retrieval
    # itself is unaffected: ``rag_block`` still grounds the answer, and the
    # retrieved titles are still logged above for RAG-quality debugging.
    # Answered mid-collection: restate the open question so the user is not left
    # wondering whether their service request survived. It did — this node writes
    # only response_message/status, so collected_data and active_field are intact
    # and the next turn continues where it left off.
    # Turn outcome for analytics: the fallback means the question was not
    # answered; a grounded (RAG-backed) reply is ANSWERED; an answer produced
    # from the static prompt alone is PARTIAL (plausible but ungrounded).
    if answer == _FALLBACK_MESSAGE:
        answer_status = "UNANSWERED"
    elif rag_block:
        answer_status = "ANSWERED"
    else:
        answer_status = "PARTIAL"

    active_field = state.get("active_field")
    if active_field:
        from app.agents.graph.nodes.handover.confirmation_node import _FIELD_LABELS

        label = _FIELD_LABELS.get(active_field, str(active_field).replace("_", " "))
        answer = f"{answer}\n\n---\n\nBack to your service request — please provide the {label}."

    return {
        "response_message": answer,
        "status": "WAITING_FOR_USER",
        "faq_sources": [],
        "answer_status": answer_status,
    }


async def _capture_faq_llm_call(
    state: ServiceRequestState,
    *,
    gateway: Any,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    parse_success: bool,
    parse_error: str | None,
) -> None:
    """Record the FAQ answer LLM call so help-agent token/cost analytics are
    complete.  Mirrors the supervisor's capture pattern; never raises."""
    tm = state.get("trace_manager")
    trace_id = _extract_uuid(state.get("trace_id"))
    if tm is None or trace_id is None:
        return
    try:
        node_run_id = _extract_uuid(state.get("_trace_node_run_id"))
        llm_run_id = await tm.start_run(
            trace_id=trace_id,
            run_name="faq_llm_call",
            run_type="LLM",
            parent_run_id=node_run_id,
        )
        if llm_run_id is None:
            return
        await tm.capture_llm_call(
            trace_id=trace_id,
            run_id=llm_run_id,
            provider=getattr(gateway, "provider_name", "openai"),
            model=gateway.model,
            temperature=Decimal("0"),
            prompt_name="faq_prompt",
            prompt_version="1.0",
            input_tokens=input_tokens or None,
            output_tokens=output_tokens or None,
            total_tokens=(input_tokens + output_tokens) or None,
            latency_ms=latency_ms or None,
            parse_success=parse_success,
            parse_error=parse_error,
        )
        await tm.finish_run(
            llm_run_id,
            status="SUCCESS" if parse_success else "FAILED",
            error_message=parse_error,
        )
    except Exception:
        log.warning("faq_node.capture_llm_call.failed", exc_info=True)


def _extract_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, AttributeError):
        return None
