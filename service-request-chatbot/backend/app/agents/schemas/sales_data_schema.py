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
        "Please provide the lease code for which you want to submit sales data "
        "(e.g. LC-1001)."
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

CSV_REQUIRED_HEADERS: list[str] = [
    "lease_code",
    "sales_period_start",
    "sales_period_end",
    "sales_amt",
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
    "SUBMITTING":             "Submitting to backend",
    "SALES_DATA_COMPLETE":    "Submission complete",
    "MANUAL_ENTRY":           "Manual entry in progress",
    "AWAITING_MANUAL_CONFIRM":"Awaiting confirmation of manual entry",
    "ADMIN_REVIEW":           "Admin review in progress",
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
})

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
