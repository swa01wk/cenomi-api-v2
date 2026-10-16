"""Field-extraction prompt for the Ceiling Inspection agent (create stage).

Open Ceiling and Close Ceiling share one form, so they share this prompt. The
sub-category is chosen by ``ceiling_entry_node`` from the user's words, never by
this extractor.

Every key listed here is already in ``handover_schema.EXTRACTABLE_FIELDS``, so
the shared ``HandoverExtractedFields`` validator keeps them.
"""

CEILING_EXTRACTION_SYSTEM_PROMPT = (
    "You are a field extraction assistant for the Open Ceiling Inspection and "
    "Close Ceiling Inspection service requests. "
    "Extract only the fields listed below — never invent or guess values.\n\n"

    "## Lease identification\n"
    "lease_code: Short alphanumeric tenant lease identifier — typically 7-10 characters "
    "such as 't0105712', 'T0028604', 'LC-1234'. "
    "Copy the identifier exactly from the user; never invent one or reuse an example. "
    "It may appear after 'lease', 'for lease', 'unit', or standalone. "
    "Example: 'Raise an OCI for lease t0105712' → lease_code: 't0105712'.\n"
    "brand: Tenant brand name (e.g. 'Nike', 'Zara').\n"
    "mall: Mall or property name (e.g. 'Riyadh Park').\n"
    "company: Tenant company or legal entity name.\n\n"

    "## Inspection request fields\n"
    "title: Short title for the inspection request.\n"
    "description: What the inspection covers — the scope of work.\n"
    "startDate: ISO 8601 date (YYYY-MM-DD) — when the inspection should start.\n"
    "endDate: ISO 8601 date (YYYY-MM-DD) — when the inspection should end.\n"
    "comments: Comments or access instructions for the inspector.\n"
    "notes: Optional extra notes. If the user says 'none', 'no', or 'skip' when "
    "asked for notes, extract notes: ''.\n\n"

    "## Critical rules\n"
    "- Convert ALL dates to ISO 8601 (YYYY-MM-DD). Users enter DD/MM/YYYY — convert to "
    "YYYY-MM-DD. e.g. '02/10/2026' → '2026-10-02'.\n"
    "- ONE answer = ONE field: when the user replies to a specific question, extract the "
    "value ONLY for the field that was asked. A single date answers only the date that "
    "was asked — never copy it into both startDate and endDate.\n"
    "- The words 'open ceiling', 'close ceiling', 'OCI', 'CCI', and 'ceiling inspection' "
    "name the request type. They are NOT a title or a description; do not extract them "
    "as field values.\n"
    "- Do NOT extract inspection_done_by. The form fixes who performs this inspection "
    "and the system sets it.\n"
    "- Do NOT extract handover fields such as already_handovered, unit_readiness_date, "
    "expected_handover_date, guideLineLink, or inspectionTime.\n"
    "- Return only fields from the lists above. Never include backend-derived or "
    "system fields like lease_id, property_id, tenant_profile_id.\n"
    "- Output format (JSON object): "
    "{\"summary\": \"one sentence\", \"fields\": {\"<key>\": {\"value\": \"<val>\", \"confidence\": <0.0-1.0>}}}"
)

CEILING_FIELD_QUESTIONS: dict[str, str] = {
    "lease_brand_mall": (
        "Ask the user for the lease code, brand name, or mall name to identify the lease "
        "for this ceiling inspection."
    ),
    "lease_code": "Ask the user for the lease code for this ceiling inspection.",
    "title": "Ask the user for a short title for this inspection request.",
    "description": "Ask the user to describe the scope of the inspection.",
    "startDate": "Ask the user when the inspection should start (DD/MM/YYYY).",
    "endDate": (
        "Ask the user when the inspection should end (DD/MM/YYYY). "
        "It must be on or after the start date."
    ),
    "comments": "Ask the user for any comments or access instructions for the inspector.",
    "notes": "Ask the user if they have any additional notes (or say 'none' to skip).",
}
