"""Deterministic detection of "I want to change field X" redirects.

When the bot is mid-flow asking for one field (``active_field``) and the user
instead asks to revisit an already-answered field ("I want to change the
title", "can you update the mall to Riyadh Park"), the LLM extractor has no
reliable way to recognise this — it is primed toward ``active_field`` and the
previous assistant question, so it tends to bind the correction phrase to the
field currently being collected instead of the one the user named.

This module provides a cheap, regex-based detector that runs *before* the LLM
extraction call so the redirect can be handled deterministically.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

# ---------------------------------------------------------------------------
# Field synonym table
# ---------------------------------------------------------------------------
# Seeded from confirmation_node._FIELD_LABELS plus the Path B synonym table in
# handover_extraction_prompt.py, generalised to every collectible field.
# Longer / more specific phrases are listed first within each field so a
# greedy longest-match search prefers the more specific field when phrases
# overlap (e.g. "actual trading date" vs "trading date").

FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "description": ("description",),
    "mall": ("mall",),
    "brand": ("brand",),
    "lease_code": ("lease code", "lease_code"),
    "startDate": (
        "inspection start date",
        "start date",
        "startdate",
        "inspection start",
        "start",
    ),
    "endDate": (
        "inspection end date",
        "end date",
        "enddate",
        "inspection end",
        "end",
    ),
    "inspection_done_by": (
        "inspection done by",
        "inspector",
        "who will inspect",
    ),
    "notes": ("notes", "note"),
    "comments": ("comments", "comment"),
    "guideLineLink": (
        "guideline link",
        "guideline",
        # Spelling variants seen in live use. The plural of each is handled
        # automatically (see _phrase_pattern), so only the stems are listed.
        # "guidline" — dropped 'e' — is the common misspelling; when it failed
        # to match, the request fell through to the unspecified-correction path
        # and re-asked whichever field happened to be active instead.
        "guidline link",
        "guidline",
        "guide line link",
        "guide line",
    ),
    "document_status": (
        "document status",
        "report status",
    ),
    "unit_readiness_date": ("unit readiness date", "readiness date"),
    "expected_handover_date": ("expected handover date",),
    "actual_handover_date": ("actual handover date",),
    "fitout_start_date": ("fitout start date", "fitout start"),
    "fitout_end_date": (
        "fitout end date",
        "fitout end",
        "fitout completion date",
        "fitout completion",
    ),
    "already_handovered": (
        "handover type",
    ),
    "actual_shop_handover_date": (
        "actual shop handover date",
        "shop handover date",
        "actual shop handover",
        "shop handover",
    ),
    "actual_fitout_start_date": (
        "actual fitout start date",
        "actual fitout start",
    ),
    "actual_fitout_end_date": (
        "actual fitout end date",
        "actual fitout end",
    ),
    "actual_trading_date": (
        "actual trading date",
        "actual trading",
        "trading start",
        "trading date",
    ),
}

# ---------------------------------------------------------------------------
# Stage-scoped synonym overrides
# ---------------------------------------------------------------------------
# The table above is written for the standard handover path (CREATE_SR →
# FM_REVIEW → RDD_REVIEW). Path B (CREATE_HANDED_OVER_SR — "Already Trading")
# records the same real-world milestones under a parallel set of ``actual_*``
# keys, and users name them exactly the same way: "the handover date", "the
# fitout start date". Resolved against the base table those land on
# ``actual_handover_date`` / ``fitout_start_date`` / ``fitout_end_date``, which
# Path B never collects — so the bot asked "What would you like to *set* as the
# Actual Handover Date?" (set, not change: the field had no value because it is
# not part of this draft) and the user's real edit went nowhere.
#
# These entries are phrase → field and are layered over the base table for the
# named stage only, so the standard path is untouched.
_STAGE_SYNONYM_OVERRIDES: dict[str, dict[str, str]] = {
    "CREATE_HANDED_OVER_SR": {
        "actual handover date": "actual_shop_handover_date",
        "handover date": "actual_shop_handover_date",
        "fitout start date": "actual_fitout_start_date",
        "fitout start": "actual_fitout_start_date",
        "fitout end date": "actual_fitout_end_date",
        "fitout end": "actual_fitout_end_date",
        "fitout completion date": "actual_fitout_end_date",
        "fitout completion": "actual_fitout_end_date",
        "trading date": "actual_trading_date",
    },
}


def _synonym_table(workflow_stage: str | None) -> dict[str, str]:
    """Return the phrase → field map in force for *workflow_stage*."""
    table = {
        phrase: field
        for field, phrases in FIELD_SYNONYMS.items()
        for phrase in phrases
    }
    table.update(_STAGE_SYNONYM_OVERRIDES.get(workflow_stage or "", {}))
    return table


# Base (imperative) forms only — "update the mall".
_VERB_BASE = r"(?:change|edit|update|modify|correct|fix)"
# Inflected forms — "updated", "editing", "changes".
_VERB_INFLECTED = (
    r"(?:chang(?:es|ed|ing)|edit(?:s|ed|ing)|updat(?:es|ed|ing)"
    r"|modif(?:ies|ied|ying)|correct(?:s|ed|ing)|fix(?:es|ed|ing))"
)
_LEAD_IN = r"(?:actually|also|now|ok|okay|hey|no|sorry|oops)?[\s,]*"

# A correction REQUEST, not merely a message that happens to contain a
# correction verb. This distinction matters: the user's *answer* to
# "what would you like to change the title to?" may legitimately be a value
# like "Updated renovation title" — treating that as a fresh correction
# request re-asks the same question forever. Only phrasings that read as a
# request qualify.
_CORRECTION_CUE_RE = re.compile(
    "|".join(
        (
            # "i want to change …", "need to make some changes in …"
            rf"\b(?:want|need|like|going|have|got)\s+to\s+(?:make\s+(?:some\s+)?)?"
            rf"(?:{_VERB_BASE}|{_VERB_INFLECTED})",
            # "can you update …", "could we change …"
            rf"\b(?:can|could|would|will|should)\s+(?:you|we|i)\s+{_VERB_BASE}\b",
            rf"\bplease\s+{_VERB_BASE}\b",
            rf"\blet\s+me\s+{_VERB_BASE}\b",
            # Bare imperative at the start: "change the title", "update mall: X".
            # Base form only — "Updated renovation title" must NOT match here.
            rf"^{_LEAD_IN}{_VERB_BASE}\b",
            # Inflected at the start only with a determiner, which reads as a
            # request ("editing the mall") rather than a value ("Updated
            # renovation title").
            rf"^{_LEAD_IN}{_VERB_INFLECTED}\s+(?:the|this|that|my|its)\b",
        )
    ),
    re.IGNORECASE,
)

# A trailing "to <value>" or ": <value>" is treated as an inline replacement
# value supplied in the same message as the redirect request.
_INLINE_VALUE_RE = re.compile(
    r"(?:\bto\b\s+(?P<to_val>\S.*)$)|(?::\s*(?P<colon_val>\S.*)$)",
    re.IGNORECASE,
)


def is_unspecified_correction(
    user_message: str,
    workflow_stage: str | None = None,
) -> bool:
    """True when the user asks to change something but names no field.

    "can you fix it", "i want to change something", a bare "update". These
    must not be treated as a value: when the bot is waiting on a field, the
    whole phrase would be stored as that field's content (observed live: the
    title became "chnage the titel").

    *workflow_stage* selects the synonym table, so a phrase that names a field
    only in the current stage (e.g. "the handover date" in Path B) counts as
    specified rather than falling through to here.
    """
    if not user_message or not user_message.strip():
        return False
    stripped = user_message.strip()
    if not _CORRECTION_CUE_RE.search(stripped):
        return False
    return _find_matched_field(stripped.lower(), workflow_stage) is None


@lru_cache(maxsize=512)
def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Whole-word regex for *phrase*, tolerating a plural on any word.

    "guideline link" matches "guidelines link", "guideline links", and
    "guidelines links". Cached because the synonym table is walked on every
    correction check.
    """
    words = [rf"{re.escape(word)}s?" for word in phrase.split()]
    body = r"\s+".join(words)
    return re.compile(rf"\b{body}\b")


def _find_matched_field(
    message_lower: str,
    workflow_stage: str | None = None,
) -> tuple[str, int] | None:
    """Return ``(field, end_index)`` for the longest synonym match, or ``None``.

    Synonyms match on whole words only. A bare substring search reads short
    synonyms out of unrelated words — "end" out of "weekend", "start" out of
    "restart", "note" out of "denote", "comment" out of "commentary" — and
    every one of those turns an ordinary sentence into a redirect to a field
    the user never mentioned.

    The longest phrase wins, so a more specific name beats a shorter one that
    it contains ("actual shop handover date" over "handover date").

    Each word also matches its plural, so the table does not need both forms of
    every noun — "guidelines link" resolves from "guideline link". Whether a
    user singularises a field name carries no meaning, and listing both by hand
    is how the plural of ``guideline`` came to be missing.
    """
    best_field: str | None = None
    best_len = 0
    best_end = 0
    for phrase, field in _synonym_table(workflow_stage).items():
        match = re.search(_phrase_pattern(phrase), message_lower)
        if match is not None and len(phrase) > best_len:
            best_field = field
            best_len = len(phrase)
            best_end = match.end()
    if best_field is None:
        return None
    return best_field, best_end


def _has_inline_value(message: str, after_index: int) -> bool:
    """True when a "to <value>" / ": <value>" trail follows the field mention."""
    return bool(_INLINE_VALUE_RE.search(message[after_index:]))


def detect_correction_intent(
    user_message: str,
    collected_data: dict[str, Any] | None,
    active_field: str | None,
    workflow_stage: str | None = None,
) -> tuple[str, bool] | None:
    """Detect a deterministic "change field X" redirect in *user_message*.

    Returns ``(field_key, has_inline_value)`` when the user is asking to
    revisit a field other than the one currently being asked, or ``None``
    when no such redirect is detected (a normal answer, or a correction verb
    with no recognised field).

    Rules:
    - Requires a correction *request* phrasing (see ``_CORRECTION_CUE_RE``),
      not merely the presence of a correction verb — otherwise a legitimate
      answer like "Updated renovation title" would be misread as a request
      to change the title, re-asking the same question forever.
    - A match on ``active_field`` itself is not a redirect — it's a normal
      answer to the question currently being asked.

    Note: the target field need not already have a value in
    ``collected_data`` — a field the user skipped earlier (e.g. an optional
    field like ``title``) has no stored value yet, but "I want to edit the
    title" is still a valid redirect. The ``active_field`` check above is
    what prevents this from misfiring on the field currently being collected.

    *workflow_stage* selects the synonym table (see
    ``_STAGE_SYNONYM_OVERRIDES``) so a phrase resolves to the key the current
    flow actually collects — "the handover date" is
    ``actual_shop_handover_date`` in Path B and ``actual_handover_date``
    everywhere else.
    """
    if not user_message or not user_message.strip():
        return None
    if not _CORRECTION_CUE_RE.search(user_message.strip()):
        return None

    message_lower = user_message.lower()
    match = _find_matched_field(message_lower, workflow_stage)
    if match is None:
        return None
    matched_field, match_end = match

    if matched_field == active_field:
        return None

    return matched_field, _has_inline_value(user_message, match_end)
