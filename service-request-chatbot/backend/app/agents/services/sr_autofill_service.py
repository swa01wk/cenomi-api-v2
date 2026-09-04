"""Generate the handover text fields the short flow no longer asks for.

In the four-turn CREATE flow the user supplies the handover type, the
inspection window and the inspector; title, description, notes and comments are
filled in for them and shown on the review card, where every one of them stays
editable.

Two levels of generation:

* **Template** — deterministic sentences built from the lease, brand, mall and
  the inspection window. Always available, never fails, and always used for the
  ``title`` (it is a platform lookup key of the shape ``handover-{lease}-{slug}``
  that users search on, so it must stay predictable).
* **AI polish** — with ``HANDOVER_AI_AUTOFILL_ENABLED`` the description and
  notes are drafted by the LLM instead. Any error, timeout or unusable answer
  falls back to the template, so the flow cannot stall on the model.

Every generated value is reported with its provenance so the review card can
label it (``template`` / ``ai``) rather than presenting invented prose as
something the user typed.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import structlog

from app.agents.schemas.handover_schema import (
    AI_FILLED_FIELDS,
    AI_FILLED_STAGES,
    STAGE_REGISTRY,
)

log = structlog.get_logger(__name__)

# Seconds to wait for the polish call before falling back to the template.
AI_POLISH_TIMEOUT_S: float = 8.0

_AI_POLISH_SYSTEM_PROMPT = """You write short, factual text for a mall handover \
service request in a property management portal.

Return ONLY a JSON object with exactly these two keys:
  "description" — one sentence, max 25 words, describing the handover \
inspection: what it is, which brand/unit and mall, and the inspection window.
  "notes" — one sentence, max 25 words, of practical remarks for the inspector \
(who is inspecting, and the window).

Rules:
- Use ONLY the facts given. Never invent unit numbers, names, dates or reasons.
- No greetings, no markdown, no placeholders, no trailing commentary.
- Plain professional English, written for a colleague who will read it on the \
service request record.
"""


# ---------------------------------------------------------------------------
# Deterministic templates
# ---------------------------------------------------------------------------


def generate_description(
    lease_code: str,
    brand: str = "",
    mall: str = "",
    already_trading: bool = False,
) -> str:
    """Human-readable default description when the user does not write one."""
    kind = "Already Trading handover" if already_trading else "Handover inspection"
    where = " at ".join(p for p in (brand.strip(), mall.strip()) if p.strip())
    if where:
        return f"{kind} for lease {lease_code} - {where}."
    return f"{kind} for lease {lease_code}."


def generate_notes(lease_code: str) -> str:
    """Default notes when the user does not write any."""
    return f"No additional notes provided for lease {lease_code}."


def generate_comments(lease_code: str) -> str:
    """Default comments when the user does not write any.

    ``comments`` is ``required: true`` on the platform HANDOVER form skeleton,
    so an empty string here shows as a blank mandatory field on the SR detail
    page. A deterministic sentence keeps the record readable.
    """
    return f"No additional comments provided for lease {lease_code}."


def generate_title(lease_code: str, description: str) -> str:
    """Build ``handover-{lease_code}-{slug}`` from lease_code and description.

    The slug is the first five significant words of the description,
    lowercased and joined with hyphens.  Non-alphanumeric characters are
    stripped so the title is URL-safe and consistent.

    When *description* is empty the trailing separator is omitted, producing
    ``handover-{lease_code}`` rather than ``handover-{lease_code}-``.
    """
    slug_words = re.sub(r"[^a-zA-Z0-9\s]", "", description.lower()).split()
    slug = "-".join(slug_words[:5])
    return f"handover-{lease_code}-{slug}" if slug else f"handover-{lease_code}"


def generate_inspection_notes(
    lease_code: str,
    inspector_label: str,
    window_label: str,
) -> str:
    """Notes that actually say something, for the short flow.

    The short flow never asks for notes, so the placeholder sentence ("No
    additional notes provided…") would be the only thing on the record. The
    inspection facts are more use to whoever opens the SR next.
    """
    parts = [p for p in (window_label, inspector_label) if p]
    if not parts:
        return generate_notes(lease_code)
    if window_label and inspector_label:
        return f"Inspection scheduled {window_label}, conducted by {inspector_label}."
    if window_label:
        return f"Inspection scheduled {window_label}."
    return f"Inspection to be conducted by {inspector_label}."


def default_inspection_times(collected: dict[str, Any]) -> dict[str, str]:
    """Clocks to store for a window the short flow never asked the time for.

    ``inspectionTime`` / ``inspectionEndTime`` are optional, but
    ``validate_required_fields`` still reports an unset one as a blocking
    "required" error, and ``api_submission_node`` refuses to submit while any
    blocking error stands. In the long flow the user is asked, so they are
    always set; the short flow has to write the value the payload builder would
    have defaulted to anyway, or nothing can be submitted at all.

    An end clock follows the start clock, matching ``resolve_start_end_hhmm``.
    """
    from app.agents.services.payload_builder_service import _DEFAULT_INSPECTION_HOUR

    fallback = f"{_DEFAULT_INSPECTION_HOUR:02d}:00"
    start = str(collected.get("inspectionTime") or "").strip()
    end = str(collected.get("inspectionEndTime") or "").strip()

    filled: dict[str, str] = {}
    if not start:
        start = fallback
        filled["inspectionTime"] = start
    if not end:
        filled["inspectionEndTime"] = start
    return filled


# ---------------------------------------------------------------------------
# Field-set helpers
# ---------------------------------------------------------------------------


def _is_unset(value: Any) -> bool:
    """True when the user has neither written nor been asked for a value."""
    return value is None or (isinstance(value, str) and value.strip() == "")


def _inspector_label(collected: dict[str, Any]) -> str:
    raw = str(collected.get("inspection_done_by") or "").strip().upper()
    return {"FM_MANAGER": "FM Manager", "OPERATIONS": "Operations"}.get(raw, "")


def _window_label(collected: dict[str, Any]) -> str:
    """``6 Sep 2026 to 7 Sep 2026`` from whatever dates are present."""
    from app.agents.services.inspection_suggestions_service import parse_date

    start = parse_date(collected.get("startDate"))
    end = parse_date(collected.get("endDate"))
    if start and end:
        return f"{start.day} {start:%b %Y} to {end.day} {end:%b %Y}"
    if start:
        return f"from {start.day} {start:%b %Y}"
    return ""


def target_fields(collected: dict[str, Any], workflow_stage: str) -> list[str]:
    """Which AI-filled fields this stage needs and the user has not supplied."""
    if workflow_stage not in AI_FILLED_STAGES:
        return []
    stage = STAGE_REGISTRY.get(workflow_stage)
    if stage is None:
        return []
    return [
        f
        for f in stage.required_fields
        if f in AI_FILLED_FIELDS and _is_unset(collected.get(f))
    ]


def build_template_values(
    collected: dict[str, Any],
    workflow_stage: str,
    fields: list[str] | None = None,
) -> dict[str, str]:
    """Deterministic values for *fields* (defaults to every outstanding one)."""
    lease_code = str(collected.get("lease_code") or "").strip()
    if not lease_code:
        # Every template is anchored on the lease; without it there is nothing
        # meaningful to write yet.
        return {}

    wanted = fields if fields is not None else target_fields(collected, workflow_stage)
    if not wanted:
        return {}

    already_trading = (
        workflow_stage == "CREATE_HANDED_OVER_SR"
        or str(collected.get("already_handovered") or "").strip().lower() == "already_trading"
    )
    values: dict[str, str] = {}

    if "description" in wanted:
        values["description"] = generate_description(
            lease_code,
            str(collected.get("brand") or ""),
            str(collected.get("mall") or ""),
            already_trading=already_trading,
        )
    if "notes" in wanted:
        values["notes"] = generate_inspection_notes(
            lease_code,
            _inspector_label(collected),
            _window_label(collected),
        )
    if "comments" in wanted:
        values["comments"] = generate_comments(lease_code)
    if "title" in wanted:
        if already_trading:
            values["title"] = f"Already Trading - {lease_code}"
        else:
            # Slug only a description the USER wrote — a generated one just
            # restates the lease code, yielding a redundant title.
            user_description = (
                str(collected.get("description") or "")
                if "description" not in wanted
                else ""
            )
            values["title"] = generate_title(lease_code, user_description)

    return values


# ---------------------------------------------------------------------------
# LLM polish
# ---------------------------------------------------------------------------


def _polish_facts(collected: dict[str, Any]) -> str:
    """The only facts the model is allowed to write from."""
    lines = [
        f"Lease code: {collected.get('lease_code') or '-'}",
        f"Brand: {collected.get('brand') or '-'}",
        f"Mall: {collected.get('mall') or '-'}",
        f"Units: {collected.get('unit_codes') or '-'}",
        f"Inspection window: {_window_label(collected) or '-'}",
        f"Inspection done by: {_inspector_label(collected) or '-'}",
    ]
    return "\n".join(lines)


async def _polish_with_llm(
    collected: dict[str, Any],
    fields: list[str],
) -> dict[str, str]:
    """Ask the LLM for description / notes. Returns ``{}`` on any problem."""
    from app.agents.llm.gateway import get_default_gateway

    wanted = [f for f in fields if f in ("description", "notes")]
    if not wanted:
        return {}

    try:
        raw, _in_tokens, _out_tokens, _latency = await asyncio.wait_for(
            get_default_gateway().complete_json(
                system_prompt=_AI_POLISH_SYSTEM_PROMPT,
                user_message=_polish_facts(collected),
            ),
            timeout=AI_POLISH_TIMEOUT_S,
        )
    except Exception:
        log.warning("sr_autofill.polish_failed", exc_info=True)
        return {}

    if not isinstance(raw, dict):
        return {}

    polished: dict[str, str] = {}
    for key in wanted:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            # Guard against a chatty model: keep it to one readable sentence.
            polished[key] = value.strip()[:500]
    return polished


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def autofill_text_fields(
    collected: dict[str, Any],
    workflow_stage: str,
    use_ai: bool | None = None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Return ``(values, provenance)`` for the fields the flow does not ask.

    *values* only ever contains fields the user has left unset, so a value the
    user typed — or edited on the review card — is never overwritten.
    *provenance* maps each generated field to ``{"source": ..., "confidence": ...}``
    for the review card's badges.
    """
    fields = target_fields(collected, workflow_stage)
    if not fields:
        return {}, {}

    values = build_template_values(collected, workflow_stage, fields)
    if not values:
        return {}, {}

    provenance: dict[str, dict[str, Any]] = {
        field: {"source": "template", "confidence": 1.0} for field in values
    }

    if use_ai is None:
        from app.core.config import get_settings

        use_ai = bool(get_settings().handover_ai_autofill_enabled)

    if use_ai:
        polished = await _polish_with_llm(collected, fields)
        for field, text in polished.items():
            if field in values:
                values[field] = text
                provenance[field] = {"source": "ai", "confidence": 0.8}
        # The title slugs the description, so regenerate it from the polished
        # text rather than leaving a slug of a sentence the user never sees.
        if "title" in values and "description" in polished:
            values["title"] = generate_title(
                str(collected.get("lease_code") or ""), polished["description"]
            )

    log.info(
        "sr_autofill.filled",
        stage=workflow_stage,
        fields=sorted(values),
        ai=bool(use_ai),
    )
    return values, provenance
