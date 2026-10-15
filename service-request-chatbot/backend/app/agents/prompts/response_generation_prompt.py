"""LLM prompt for the response generation node.

This is the only place in the graph where the assistant's voice is composed.
Every other node sets machine-readable signals; this node turns those signals
into a natural, context-aware message for the user.
"""

from typing import Any

# ---------------------------------------------------------------------------
# Field name → human-readable label mapping (never expose raw field keys)
# ---------------------------------------------------------------------------

FIELD_LABELS: dict[str, str] = {
    "lease_code": "lease code",
    "lease_brand_mall": "lease, brand, or mall",
    "description": "description",
    "startDate": "inspection start date",
    "inspectionTime": "inspection start time",
    "endDate": "inspection end date",
    "inspectionEndTime": "inspection end time",
    "inspection_done_by": "who will perform the inspection (FM Manager or Operations)",
    "comments": "comments",
    "unit_readiness_date": "unit readiness date",
    # The three fitout confirmation milestones. Without these, _mentions_field
    # fell back to the raw key and demanded the word "done" — which none of the
    # real questions contain ("Has the fitout been completed?") — so a correct
    # reply was judged a repeat of the previous question and replaced by a
    # sentence built from the key: "Please provide the fitout complete done."
    #
    # _mentions_field requires every token of the label (>3 chars) to appear in
    # the reply, so these are worded to match the questions in _RADIO_PHRASING
    # rather than to read prettily: "started" matches "Has the fitout started?",
    # "completed" matches "Has the fitout been completed?".
    "fitout_start_done": "fitout started (yes or no)",
    "fitout_complete_done": "fitout completed (yes or no)",
    "trading_done": "trading started (yes or no)",
    "delay_reason": "reason for the delay",
    "planned_fitout_start_date": "revised planned fitout start date",
    "planned_fitout_end_date": "revised planned fitout end date",
    "planned_fitout_complete_date": "revised planned fitout completion date",
    "planned_trading_date": "revised planned trading date",
    "actual_fitout_complete_date": "actual fitout completion date",
    "expected_handover_date": "expected handover date",
    "guideLineLink": "guideline link",
    "actual_handover_date": "actual handover date",
    "fitout_start_date": "fitout start date",
    "fitout_end_date": "fitout end date",
    "trading_date": "trading date",
    "brand": "brand name",
    "mall": "mall name",
    "city": "city",
    "unit_codes": "unit codes",
    "contracted_area": "contracted area",
    # Work Permit portal form fields
    "work_permit_type": "work permit type",
    "department": "department",
    "unit_type": "unit type",
    "owner": "owner",
    "trade_name": "trade name",
    "responsible_person": "responsible person",
    "phone": "phone number",
    "type_work": "type of work",
    "list_equipment": "list of equipment",
    "work_info": "work info",
    "notes": "notes",
    "contractor_name": "contractor name",
    "work_type": "work type",
    "work_location": "work location",
    "demand_appendices": "demand appendices",
    "special_conditions": "special conditions",
    "planneddate": "planned date and time",
    "workers": "workers",
    "measureofsafety": "measure of safety",
    "typeofwork": "type of work",
    "effective_starting": "effective starting dates",
    "firstperiod": "first period",
    "secondperiod": "second period",
    "entry_gate": "entry gate",
}

# ---------------------------------------------------------------------------
# Stage descriptions
# ---------------------------------------------------------------------------

STAGE_DESCRIPTIONS: dict[str, str] = {
    "CREATE_SR": "creating a new Handover Service Request",
    "FM_REVIEW": "FM Manager review of the handover",
    "RDD_REVIEW": "RDD Project Manager review and final sign-off",
}

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

RESPONSE_GENERATION_SYSTEM_PROMPT = """
You are a professional, warm, and concise assistant for the Cenomi Malls Service Request platform.
You help mall managers and operations staff create and manage Handover Service Requests for tenant fit-outs.

════════════════════════════════════════════════════════
YOUR ROLE IN THIS TURN
════════════════════════════════════════════════════════

You receive structured context about what just happened in the workflow and your
job is to compose a single, natural reply that the user sees in the chat UI.

════════════════════════════════════════════════════════
TONE AND STYLE RULES
════════════════════════════════════════════════════════

- Be conversational and professional — like a knowledgeable colleague, not a form wizard.
- Be concise: 1–3 sentences for most replies. Do not pad with filler phrases.
- Personalise using real names from the context (brand, mall, lease code) when available.
- Acknowledge progress: if data was just collected, briefly confirm it before asking for the next item.
- Ask only ONE question per turn. Never list multiple questions at once.
- When WHAT TO COMMUNICATE asks for a specific field, ask for THAT field only.
  Do not substitute a different date field (e.g. do not ask for fitout start when
  the intent is actual handover date). When confirming a value just collected,
  name the field that was actually filled — never invent a different label.
- **CRITICAL — do NOT interpret "none"/"skip"/"no thanks" as applying to a
  field OTHER than the one the WHAT TO COMMUNICATE says to ask next.** Example:
  if the previous turn asked for the *title* and the user replied "none", and
  the current intent says "ask for description", the correct reply is to ask
  for description directly ("Please share a short description for this handover
  request, or reply 'none' to skip.") — DO NOT say "I'll skip the description"
  or acknowledge skipping any field the user did not explicitly name. The
  user's "none" was consumed by the previously asked field; you have no basis
  to claim a different field was skipped.
- When there are VALIDATION ISSUES in the context, focus EXCLUSIVELY on the
  validation error. Do NOT also ask for missing fields in the same message.
  Address the invalid value first; the user can provide the remaining fields
  after the error is resolved.
- When a lease is found, celebrate it briefly then move on (e.g. "Got it — found your [Brand] lease at [Mall].").
- When something goes wrong, be empathetic and specific about what to try.
- Never mention internal field names like `startDate`, `tenant_profile_id`, `brand_id`, etc.
  Always use their plain English equivalents from the context you are given.
- Never reveal workflow internals (node names, stages, status codes).
- If the user is confirming or cancelling, acknowledge their choice warmly.
- If the message to communicate contains ``[Source: ...]`` citation markers, preserve them
  verbatim at the end of your reply — do not remove or paraphrase them.

════════════════════════════════════════════════════════
DETAILS CARD — OPTIONAL
════════════════════════════════════════════════════════

You may show stored details as a card instead of writing them into the sentence.
- Add a "card" only when the user asks to see, list, or review stored details
  that read better as label/value rows than as a sentence — e.g. a list of
  people, a schedule, or a group of related fields from DATA COLLECTED SO FAR.
- Never add a card when asking a question, confirming, reporting an error,
  answering with a single value, or when a UI component (other than "message")
  is already being shown.
- "title": a short heading, e.g. "Workers (2)" or "Schedule".
- "fields": label/value pairs whose values are copied from DATA COLLECTED SO FAR,
  as plain text. For several items (e.g. people), repeat the same labels for
  each item in turn.
- "files": only documents listed under AVAILABLE DOCUMENTS, referenced by their
  doc_N handle with a short label. Never invent a handle.
- With a card, "message" is one short sentence introducing it; do not repeat the
  card's data in the message.
- Never put a doc_N handle or any ID in "message" or in a field value.

════════════════════════════════════════════════════════
OUTPUT FORMAT — STRICT
════════════════════════════════════════════════════════

Return ONLY a JSON object with a "message" key and, only when a card helps, a "card" key:

  {"message": "<your natural language response here>"}

  {"message": "<one short sentence>",
   "card": {"title": "<heading>",
            "fields": [{"label": "<label>", "value": "<value>"}],
            "files": [{"label": "<label>", "ref": "doc_1"}]}}

Do NOT include markdown, other keys, or any text outside the JSON object.
""".strip()


_DOCUMENT_ID_KEYS = frozenset({"document_id", "documentId"})


def _document_ids(value: Any) -> list[str]:
    """Every document id at any depth, in first-seen order."""
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k in _DOCUMENT_ID_KEYS:
                if str(v or "").strip():
                    found.append(str(v).strip())
            else:
                found += _document_ids(v)
    elif isinstance(value, list):
        for v in value:
            found += _document_ids(v)
    return found


def build_document_refs(
    documents: list[dict] | None, collected_data: dict | None
) -> dict[str, str]:
    """Real document id → ``doc_N`` handle; the LLM only ever sees the handle."""
    refs: dict[str, str] = {}
    for doc_id in _document_ids(documents or []) + _document_ids(collected_data or {}):
        refs.setdefault(doc_id, f"doc_{len(refs) + 1}")
    return refs


def document_file_names(documents: list[dict] | None) -> dict[str, str]:
    """Real document id → its file name, for rows that have one."""
    names: dict[str, str] = {}
    for doc in documents or []:
        if not isinstance(doc, dict):
            continue
        doc_id = str(doc.get("document_id") or doc.get("documentId") or "").strip()
        name = str(
            doc.get("file_name") or doc.get("fileName") or doc.get("document_name") or ""
        ).strip()
        if doc_id and name:
            names[doc_id] = name
    return names


def _with_document_refs(value: Any, doc_refs: dict[str, str]) -> Any:
    """Document ids at any depth become their ``doc_N`` handle (dropped when unknown)."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in _DOCUMENT_ID_KEYS:
                ref = doc_refs.get(str(v or "").strip())
                if ref:
                    out["document"] = ref
                continue
            out[k] = _with_document_refs(v, doc_refs)
        return out
    if isinstance(value, list):
        return [_with_document_refs(v, doc_refs) for v in value]
    return value


def _readable(value: Any) -> str:
    """Nested rows as "key: value, key: value; …" rather than a Python repr."""
    if isinstance(value, dict):
        return ", ".join(
            f"{k}: {shown}" for k, v in value.items() if (shown := _readable(v))
        )
    if isinstance(value, list):
        parts = [shown for v in value if (shown := _readable(v))]
        sep = "; " if any(isinstance(v, (dict, list)) for v in value) else ", "
        return sep.join(parts)
    return "" if value is None else str(value).strip()


def build_response_generation_context(
    *,
    user_message: str,
    response_intent: str,
    workflow_stage: str | None,
    intent: str | None,
    collected_data: dict,
    missing_fields: list[str],
    validation_errors: list[dict],
    confirmation_status: str | None,
    response_ui_type: str | None,
    conversation_history: list[dict],
    asking_field: str | None = None,
    doc_refs: dict[str, str] | None = None,
    documents: list[dict] | None = None,
) -> str:
    """Build the user-content string sent to the LLM for response generation."""

    doc_refs = doc_refs or {}
    lines: list[str] = []

    # ── 1. What the system determined needs to be communicated ──────────────
    lines.append("## WHAT TO COMMUNICATE TO THE USER")
    lines.append(response_intent or "Continue the conversation naturally.")
    if asking_field:
        field_label = FIELD_LABELS.get(asking_field, asking_field)
        lines.append(
            f"IMPORTANT: The next question must ask for: {field_label} "
            f"(internal key: {asking_field}). Do not ask for a different field."
        )
    lines.append("")

    # ── 2. Workflow context ──────────────────────────────────────────────────
    lines.append("## WORKFLOW CONTEXT")
    stage_label = STAGE_DESCRIPTIONS.get(workflow_stage or "", workflow_stage or "unknown")
    lines.append(f"Current task: {stage_label}")
    if intent:
        lines.append(f"User intent: {intent.replace('_', ' ').title()}")
    if intent == "PREVIEW_SERVICE_REQUEST":
        lines.append(
            "NOTE: The user asked to PREVIEW the service request. "
            "Render a clear, friendly summary of all data collected so far. "
            "If no data has been collected, say so and offer to help."
        )
    if confirmation_status:
        lines.append(f"Confirmation status: {confirmation_status}")
    if response_ui_type:
        lines.append(f"UI component being shown: {response_ui_type}")
    lines.append("")

    # Fields that are internal / system-derived and must never be surfaced to
    # the user — neither in the DATA COLLECTED block nor in STILL NEEDED.
    # ``title`` is auto-generated from lease_code + description; the backend
    # IDs (brand_id, lease_id, etc.) are lookup artefacts that have no meaning
    # in a user-facing conversation.
    _non_user_fields = {
        "title",
        "tenant_profile_id",
        "property_id",
        "lease_id",
        "brand_id",
        "contract_id",
        "unit_codes",
        "city",
        "contracted_area",
        "lease_brand_mall",
        "lease",
        "mall",
        "brand",
    }

    # ── 3. Data collected so far ─────────────────────────────────────────────
    if collected_data:
        readable_data: dict[str, str] = {}
        for k, v in collected_data.items():
            if v is None or v == "" or v == []:
                continue
            if k in _non_user_fields:
                continue
            label = FIELD_LABELS.get(k, k)
            shown = _readable(_with_document_refs(v, doc_refs))
            if shown:
                readable_data[label] = shown
        if readable_data:
            lines.append("## DATA COLLECTED SO FAR")
            for label, val in readable_data.items():
                lines.append(f"- {label}: {val}")
            lines.append("")

    if doc_refs:
        names = document_file_names(documents)
        lines.append("## AVAILABLE DOCUMENTS")
        for doc_id, ref in doc_refs.items():
            lines.append(f"- {ref}: {names.get(doc_id) or 'Document'}")
        lines.append("")

    # ── 4. Still missing ─────────────────────────────────────────────────────
    # Suppress missing fields when there are blocking validation errors so the
    # response LLM focuses exclusively on asking the user to fix the invalid
    # value rather than also asking for other fields.
    if missing_fields and not validation_errors:
        user_facing_missing = [
            FIELD_LABELS.get(f, f) for f in missing_fields
            if f not in _non_user_fields
        ]
        if user_facing_missing:
            lines.append("## STILL NEEDED FROM USER")
            for field_label in user_facing_missing:
                lines.append(f"- {field_label}")
            lines.append("")

    # ── 5. Validation errors ─────────────────────────────────────────────────
    if validation_errors:
        lines.append("## VALIDATION ISSUES")
        for err in validation_errors:
            field_label = FIELD_LABELS.get(err.get("field", ""), err.get("field", "unknown field"))
            lines.append(f"- {field_label}: {err.get('message', 'invalid value')}")
        lines.append("")

    # ── 6. User's current message ────────────────────────────────────────────
    lines.append("## USER'S CURRENT MESSAGE")
    lines.append(f'"{user_message}"')
    lines.append("")

    # ── 7. Recent conversation history ───────────────────────────────────────
    if conversation_history:
        lines.append("## CONVERSATION SO FAR (oldest first)")
        for turn in conversation_history[-8:]:
            role = turn.get("role", "unknown").capitalize()
            content = turn.get("content", "")
            if content:
                lines.append(f"{role}: {content}")
        lines.append("")
        lines.append(
            "NOTE: Every question in CONVERSATION SO FAR has already been "
            "answered. Never repeat or rephrase an earlier question, and never "
            "quote a field value from history — the values above in DATA "
            "COLLECTED SO FAR are the current ones."
        )
        lines.append("")

    # ── 8. Final binding constraint ──────────────────────────────────────────
    # Deliberately LAST: the conversation history above is the most salient
    # text for the model, and it was observed re-asking the previous turn's
    # question (quoting a now-stale value) despite the same constraint being
    # stated near the top. Restating it here, after the history, is what keeps
    # the generated question aligned with the field the backend is collecting.
    if asking_field:
        field_label = FIELD_LABELS.get(asking_field, asking_field)
        lines.append(
            f"## REQUIRED: your reply MUST ask the user for {field_label}. "
            f"Asking for anything else — especially re-asking a question that "
            f"appears in CONVERSATION SO FAR — is incorrect."
        )
        lines.append("")

    lines.append(
        "Now write a single natural reply for the assistant. "
        "Respond ONLY with the JSON object from OUTPUT FORMAT: "
        "{\"message\": \"<your reply>\"}, plus a \"card\" only when the user "
        "asked to see stored details that read better as rows."
    )

    return "\n".join(lines)
