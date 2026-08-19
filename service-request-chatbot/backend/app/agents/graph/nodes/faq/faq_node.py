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
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.llm.gateway import get_default_gateway
from app.agents.prompts.faq_prompt import FAQ_SYSTEM_PROMPT
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


def _build_faq_user_content(state: ServiceRequestState, rag_block: str) -> str:
    """Build the user-facing prompt content for the FAQ node.

    Prepends the RAG context block (if any) before the user question and the
    last 4 conversation turns so the model answers follow-ups accurately.
    """
    message = state.get("user_message") or ""
    parts: list[str] = []

    if rag_block:
        parts.append(rag_block)

    parts.append(f"User question: {message}")

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
            search_kwargs: dict = {"query": user_message, "lang": lang}
            if url_pattern:
                search_kwargs["url_filter"] = url_pattern
            try:
                results = await search_repo.search(**search_kwargs)
            except TypeError:
                # Fallback: implementation doesn't accept url_filter — retry without it.
                results = await search_repo.search(query=user_message, lang=lang)
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

    try:
        parsed, _in, _out, _ms = await gateway.complete_json(
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
        log.warning("faq_node.json_parse_error", error=str(exc))
    except Exception as exc:
        log.exception("faq_node.llm_failed", error=str(exc))

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
    return {
        "response_message": answer,
        "status": "WAITING_FOR_USER",
        "faq_sources": [],
    }
