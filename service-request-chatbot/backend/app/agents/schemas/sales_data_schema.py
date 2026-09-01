"""Sales Data Agent schema.

Defines the field set, validation rules, and missing-field questions for the
Sales Data Agent.  Consumed by the agent's own nodes rather than the shared
pipeline (field_extraction / validation / missing_field) because the sales
data flow is CSV-first, not form-collection.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Manual entry fields
# ---------------------------------------------------------------------------

MANUAL_ENTRY_REQUIRED_FIELDS: list[str] = [
    "lease_code",
    "sales_period_start",
    "sales_period_end",
    "sales_amt",
]

MANUAL_ENTRY_FIELD_QUESTIONS: dict[str, str] = {
    "lease_code": (
        "Please provide the lease code for which you want to submit sales data."
    ),
    "sales_period_start": (
        "What is the start date of the sales period? (YYYY-MM-DD, e.g. 2026-08-01)"
    ),
    "sales_period_end": (
        "What is the end date of the sales period? (YYYY-MM-DD, e.g. 2026-08-31)"
    ),
    "sales_amt": (
        "What is the total sales amount in SAR for this period? "
        "Please enter a number greater than 0."
    ),
}

# ---------------------------------------------------------------------------
# CSV template required headers
# ---------------------------------------------------------------------------

# Minimum columns for an uploaded CSV. Brand / Property are optional
# (legacy tenant files omit them). Download template still includes them.
CSV_REQUIRED_HEADERS: list[str] = [
    "Lease code",
    "Start date",
    "End date",
    "Sales amount",
]

CSV_MAX_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 MB

# ---------------------------------------------------------------------------
# Workflow stages
# ---------------------------------------------------------------------------

SALES_DATA_STAGES = {
    "UPLOAD_INTENT":          "Collecting upload intent",
    "CSV_LAYER1_VALIDATION":  "Validating CSV format",
    "CSV_LAYER2_VALIDATION":  "Validating lease access and pending rows",
    "AWAITING_TABLE_CONFIRM": "Awaiting user confirmation of the review table",
    "AWAITING_PERIOD_MISMATCH_CONFIRM": (
        "CSV month differs from the month the tenant asked to upload"
    ),
    "PENDING_MONTHS_CHECK": "Checking other pending months on the same lease",
    "AWAITING_PENDING_MONTHS_CHOICE": (
        "Listing PENDING months so the tenant can pick one or upload a CSV"
    ),
    "AWAITING_OTHER_MONTHS_REMINDER": (
        "Reminding the tenant that the same lease has other PENDING months"
    ),
    "SUBMITTING":             "Submitting to backend",
    "SALES_DATA_COMPLETE":    "Submission complete",
    "MANUAL_ENTRY":           "Manual entry in progress",
    "AWAITING_MANUAL_CONFIRM":"Awaiting confirmation of manual entry",
    "ADMIN_REVIEW":           "Admin review in progress",
    "DOC_SELECT":             "Selecting the row that needs a document",
    "AWAITING_DOC_UPLOAD":    "Waiting for PDF / supporting document",
    "DOC_UPLOADING":          "Uploading document to /files",
    "AWAITING_DOC_CONFIRM":   "Awaiting confirmation of document upload",
    "DOC_SUBMITTING":         "Patching status after document upload",
}

TERMINAL_STAGES: frozenset[str] = frozenset({
    "SALES_DATA_COMPLETE",
})

# ---------------------------------------------------------------------------
# Role → permitted intents
# ---------------------------------------------------------------------------

TENANT_INTENTS: frozenset[str] = frozenset({
    "UPLOAD_SALES_DATA",
    "ENTER_SALES_DATA_MANUAL",
    "CHECK_SALES_DATA_STATUS",
    "UPLOAD_SUPPORTING_DOCUMENT",
})

# NEED_* row → PUT /files document_type_id  (matches portal DocTypeId)
DOC_TYPE_BY_STATUS: dict[str, str] = {
    "NEED_REUPLOAD": "SD_RE_UPL",
    "NEED_UPLOAD_SUPPORTING_DOCUMENT": "SD_SUP_DOC",
    "NEED_UPLOAD_AUDITED_SALES": "SD_AUD_DOC",
}

# NEED_* row → tenant PATCH status  (matches portal editSalesAmt uploadStatus)
TENANT_RESPONSE_STATUS: dict[str, str] = {
    "NEED_REUPLOAD": "RE_UPLOADED",
    "NEED_UPLOAD_SUPPORTING_DOCUMENT": "UPLOADED_SUPPORTING_DOCUMENT",
    "NEED_UPLOAD_AUDITED_SALES": "UPLOADED_AUDITED_SALES",
}

TENANT_ACTION_STATUSES: frozenset[str] = frozenset(DOC_TYPE_BY_STATUS)

# Portal Ask Upload radio labels (adminAction / editSalesAmt navigationMap)
ASK_UPLOAD_LABELS: dict[str, str] = {
    "NEED_REUPLOAD": "Sales data",
    "NEED_UPLOAD_SUPPORTING_DOCUMENT": "Supporting document",
    "NEED_UPLOAD_AUDITED_SALES": "Audited sales data",
}

ADMIN_ACTION_LABELS: dict[str, str] = {
    "APPROVED": "Accept",
    "NEED_REUPLOAD": "Ask upload — Sales data",
    "NEED_UPLOAD_SUPPORTING_DOCUMENT": "Ask upload — Supporting document",
    "NEED_UPLOAD_AUDITED_SALES": "Ask upload — Audited sales data",
}

# Tenant-facing file prompt copy (portal uses one Upload File modal; labels differ)
ASK_UPLOAD_COPY: dict[str, dict[str, str]] = {
    "NEED_REUPLOAD": {
        "title": "Sales data",
        "file_noun": "sales data file",
        "cta": "Re-upload sales data",
        "prompt_verb": "the corrected **sales data** file",
    },
    "NEED_UPLOAD_SUPPORTING_DOCUMENT": {
        "title": "Supporting document",
        "file_noun": "supporting document",
        "cta": "Upload supporting document",
        "prompt_verb": "the requested **supporting document**",
    },
    "NEED_UPLOAD_AUDITED_SALES": {
        "title": "Audited sales data",
        "file_noun": "audited sales data",
        "cta": "Upload audited sales",
        "prompt_verb": "the **audited sales data** file",
    },
}

ADMIN_INTENTS: frozenset[str] = frozenset({
    "REVIEW_SALES_DATA",
    "APPROVE_SALES_DATA",
    "CHECK_SALES_DATA_STATUS",
})

SALES_DATA_ROLE_INTENTS: dict[str, frozenset[str]] = {
    "TENANT_CSV_UPLOADER": TENANT_INTENTS,
    "TENANT_DOC_UPLOADER": TENANT_INTENTS,
    "SALES_DATA_APPROVER": ADMIN_INTENTS,
    "ADMIN": TENANT_INTENTS | ADMIN_INTENTS,
}

# ---------------------------------------------------------------------------
# Status display labels
# ---------------------------------------------------------------------------

STATUS_LABELS: dict[str, str] = {
    "PENDING":                      "⏳ Pending",
    "SUBMITTED":                    "✅ Submitted",
    "APPROVED":                     "✅ Approved",
    "NEED_REUPLOAD":                "🔄 Needs Re-upload",
    "NEED_UPLOAD_SUPPORTING_DOCUMENT": "📎 Supporting Document Required",
    "NEED_UPLOAD_AUDITED_SALES":    "📋 Audited Sales Required",
    "RE_UPLOADED":                  "✅ Re-uploaded",
    "UPLOADED_SUPPORTING_DOCUMENT": "✅ Supporting Document Uploaded",
    "UPLOADED_AUDITED_SALES":       "✅ Audited Sales Uploaded",
}

# Portal admin table (StatusHighlighterSalesData): badge collapses; tooltip is specific.
ADMIN_TABLE_STATUS_LABELS: dict[str, str] = {
    "PENDING": "Pending",
    "NEED_REUPLOAD": "Pending",
    "NEED_UPLOAD_SUPPORTING_DOCUMENT": "Pending",
    "NEED_UPLOAD_AUDITED_SALES": "Pending",
    "SUBMITTED": "Submitted",
    "UPLOADED_SUPPORTING_DOCUMENT": "Submitted",
    "RE_UPLOADED": "Submitted",
    "UPLOADED_AUDITED_SALES": "Submitted",
    "APPROVED": "Approved",
}

ADMIN_TABLE_STATUS_TOOLTIPS: dict[str, str] = {
    "PENDING": "Pending",
    "NEED_REUPLOAD": "Need Reupload",
    "NEED_UPLOAD_SUPPORTING_DOCUMENT": "Supporting Document Requested",
    "NEED_UPLOAD_AUDITED_SALES": "Audited Sales Data Requested",
    "SUBMITTED": "Submitted",
    "UPLOADED_SUPPORTING_DOCUMENT": "Uploaded Supporting Document",
    "UPLOADED_AUDITED_SALES": "Uploaded Audited Sales",
    "RE_UPLOADED": "Sales Data Reuploaded",
    "APPROVED": "Approved",
}
