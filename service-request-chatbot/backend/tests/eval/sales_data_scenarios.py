"""Sales Data Agent evaluation scenarios.

TIER STRUCTURE
--------------
  Tier sd0  Smoke      (~2 min)  SD200–SD201: intent + RBAC smoke
  Tier sd1  Happy      (~5 min)  SD210–SD216: manual, status, admin, docs
  Tier sd2  Edge       (~5 min)  SD220–SD223: future date, zero amt, cancel, stale
  Tier sd3  Admin      (~3 min)  SD230–SD235: approve, RBAC, ask-upload

CSV upload turns use ``pre_upload_csv=True`` on the first turn; the runner
calls POST /api/v2/sales-data/upload with a minimal in-memory CSV and injects
the returned session_id as ``corrected_fields.csv_session_id``.

Mock backend credentials (seeded by seed_users.py + mock-backend/seed_data.py):
  tenant.csv@cenomi.com   / test1234  → TENANT_CSV_UPLOADER
    (LC-1001/1002 PENDING, LC-1003 NEED_REUPLOAD, LC-1004 supporting doc, LC-1005 audited)
  sales.approver@cenomi.com / test1234 → SALES_DATA_APPROVER
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Dynamic date helpers
# ---------------------------------------------------------------------------

_TODAY = datetime.date.today()


def _d(days: int) -> str:
    return (_TODAY + datetime.timedelta(days=days)).isoformat()


# Sales periods — always past months for valid submissions
_PERIOD_START = "2026-08-01"   # August 2026 start
_PERIOD_END   = "2026-08-31"   # August 2026 end
_FUTURE_START = _d(10)         # 10 days in the future (invalid)
_FUTURE_END   = _d(40)

# ---------------------------------------------------------------------------
# Scenario / Turn models (extended from scenarios.py pattern)
# ---------------------------------------------------------------------------


@dataclass
class SalesDataTurn:
    """One turn in a sales-data eval scenario."""

    message: str
    note: str = ""

    # ── Hard assertions ──────────────────────────────────────────────────────
    expect_ui_type: Optional[str] = None
    """Expected response_ui.type."""

    expect_workflow_stage: Optional[str | list[str]] = None
    """Expected state.workflow_stage (string or list of acceptable stages)."""

    # ── Soft assertions ──────────────────────────────────────────────────────
    expect_keywords: list[str] = field(default_factory=list)
    """At least one keyword must be present (case-insensitive) in response message."""

    expect_no_keywords: list[str] = field(default_factory=list)
    """None of these keywords may appear in response message."""

    # ── API extras ───────────────────────────────────────────────────────────
    action: Optional[str] = None
    corrected_fields: Optional[dict] = None

    # ── CSV upload setup ─────────────────────────────────────────────────────
    pre_upload_csv: bool = False
    """If True, the runner uploads a test CSV before this turn and injects
    corrected_fields.csv_session_id so csv_validation_node can find it."""

    csv_rows: Optional[list[dict]] = None
    """Rows to include in the test CSV. Defaults to one valid row for LC-1001."""

    pre_upload_doc: bool = False
    """If True, the runner POSTs a tiny PDF to /api/v2/sales-data/supporting-doc
    and injects corrected_fields.doc_session_id."""

    active_notification_id: Optional[dict] = None
    """Simulates the turn arriving from a bell/inbox notification Open —
    e.g. {"id": "LC-2001", "key": "SALES_DATA_SUBMITTED", "message": "..."}.
    Sent verbatim as active_notification_id on the chat payload."""


@dataclass
class SalesDataScenario:
    id: int
    name: str
    goal: str
    turns: list[SalesDataTurn]
    tier: str = "sd1"      # sd0, sd1, sd2, sd3
    enabled: bool = True
    tags: list[str] = field(default_factory=list)
    user_email: str = "tenant.csv@cenomi.com"
    user_password: str = "test1234"


# ---------------------------------------------------------------------------
# Default test CSV rows
# ---------------------------------------------------------------------------

_DEFAULT_CSV_ROWS = [
    {
        "lease_code": "LC-1001",
        "sales_period_start": _PERIOD_START,
        "sales_period_end": _PERIOD_END,
        "sales_amt": "85000",
    }
]

_TWO_ROW_CSV = [
    {
        "lease_code": "LC-1001",
        "sales_period_start": _PERIOD_START,
        "sales_period_end": _PERIOD_END,
        "sales_amt": "85000",
    },
    {
        "lease_code": "LC-1002",
        "sales_period_start": _PERIOD_START,
        "sales_period_end": _PERIOD_END,
        "sales_amt": "120000",
    },
]

_FUTURE_DATE_CSV_ROWS = [
    {
        "lease_code": "LC-1001",
        "sales_period_start": _FUTURE_START,
        "sales_period_end": _FUTURE_END,
        "sales_amt": "85000",
    }
]

_ZERO_AMOUNT_CSV_ROWS = [
    {
        "lease_code": "LC-1001",
        "sales_period_start": _PERIOD_START,
        "sales_period_end": _PERIOD_END,
        "sales_amt": "0",
    }
]


# =============================================================================
# TIER sd0 — Smoke (SD200–SD201)
# =============================================================================

SD200 = SalesDataScenario(
    id=200,
    name="Sales Data — Upload Intent Smoke",
    goal="Typing 'upload sales data' routes to UPLOAD_INTENT and returns upload prompt UI.",
    tier="sd0",
    tags=["smoke", "intent", "upload"],
    turns=[
        SalesDataTurn(
            message="I want to upload my sales data",
            note="Should classify as UPLOAD_SALES_DATA → UPLOAD_INTENT stage",
            expect_ui_type="pending_months_list",
            expect_workflow_stage="AWAITING_PENDING_MONTHS_CHOICE",
            expect_keywords=["pending", "month"],
        ),
    ],
)

SD201 = SalesDataScenario(
    id=201,
    name="Sales Data — RBAC Denial Smoke",
    goal="A MALL_MANAGER (RDD role) gets denied for UPLOAD_SALES_DATA intent.",
    tier="sd0",
    tags=["smoke", "rbac"],
    user_email="aisha@cenomi.com",    # MALL_MANAGER — no sales data permissions
    user_password="test1234",
    turns=[
        SalesDataTurn(
            message="I want to upload sales data for August",
            note="MALL_MANAGER lacks UPLOAD_SALES_DATA permission — should be denied",
            expect_keywords=["not authorised", "not authorized", "don't have", "cannot", "permission", "doesn't allow", "does not allow", "role"],
        ),
    ],
)

TIER_SD0 = [SD200, SD201]


# =============================================================================
# TIER sd1 — Happy path (SD210–SD212)
# =============================================================================

SD210 = SalesDataScenario(
    id=210,
    name="Sales Data — Manual Entry Happy",
    goal="Tenant types a single sales record in natural language, agent extracts fields, "
         "tenant confirms, submission succeeds.",
    tier="sd1",
    tags=["happy", "manual-entry"],
    turns=[
        SalesDataTurn(
            message="I want to enter sales data for August 2026 for lease LC-1001, "
                    "the amount is SAR 85000",
            note="Agent should extract lease, period and amount from free text",
            expect_workflow_stage="AWAITING_MANUAL_CONFIRM",
            expect_keywords=["LC-1001", "August", "85"],
        ),
        SalesDataTurn(
            message="Yes, that's correct, please submit it",
            note="User confirms → submission_success",
            action="confirm_manual_entry",
            expect_ui_type="submission_success",
            expect_keywords=["success", "submitted", "saved"],
        ),
    ],
)

SD211 = SalesDataScenario(
    id=211,
    name="Sales Data — Status Check Happy",
    goal="Tenant asks for current submission status and gets a status_summary card.",
    tier="sd1",
    tags=["happy", "status"],
    turns=[
        SalesDataTurn(
            message="Can you show me the status of my sales data submissions?",
            note="CHECK_SALES_DATA_STATUS → STATUS_LOOKUP → status_summary UI",
            expect_ui_type="status_summary",
            expect_keywords=["LC-1001", "pending", "status"],
        ),
    ],
)

SD212 = SalesDataScenario(
    id=212,
    name="Sales Data — Admin Review Happy",
    goal="Admin logs in, requests sales data review, sees admin_sales_data_list, approves one row.",
    tier="sd1",
    tags=["happy", "admin", "approve"],
    user_email="sales.approver@cenomi.com",
    turns=[
        SalesDataTurn(
            message="Show me submitted sales data for review",
            note="REVIEW_SALES_DATA → ADMIN_REVIEW → admin_sales_data_list UI",
            expect_ui_type="admin_sales_data_list",
            expect_keywords=["LC-2001", "submitted"],
        ),
        SalesDataTurn(
            message="Approve LC-2001",
            note="Admin approves → admin_confirm UI",
            expect_ui_type="admin_confirm",
            expect_keywords=["LC-2001", "approve"],
        ),
        SalesDataTurn(
            message="Confirmed",
            note="Admin confirms approval → admin_action_success",
            action="confirm_admin_action",
            expect_keywords=["approved", "success"],
        ),
    ],
)

SD213 = SalesDataScenario(
    id=213,
    name="Sales Data — NEED_REUPLOAD PDF Happy",
    goal="Tenant responds to NEED_REUPLOAD on LC-1003 by attaching a PDF, confirming, and PATCHing RE_UPLOADED.",
    tier="sd1",
    tags=["happy", "supporting-doc", "reupload"],
    turns=[
        SalesDataTurn(
            message="I need to re-upload a PDF for lease LC-1003",
            note="UPLOAD_SUPPORTING_DOCUMENT → prompt for PDF on LC-1003",
            expect_ui_type="supporting_doc_prompt",
            expect_workflow_stage="AWAITING_DOC_UPLOAD",
            expect_keywords=["LC-1003", "PDF"],
        ),
        SalesDataTurn(
            message="I've attached the corrected PDF",
            note="Cached PDF → PUT /files → confirmation card",
            action="doc_uploaded",
            pre_upload_doc=True,
            expect_ui_type="supporting_doc_confirm",
            expect_workflow_stage="AWAITING_DOC_CONFIRM",
            expect_keywords=["LC-1003", "re-uploaded", "confirm"],
        ),
        SalesDataTurn(
            message="Yes, submit it",
            note="Confirm → PATCH RE_UPLOADED",
            action="confirm_supporting_doc",
            corrected_fields={"comment_text": "Corrected figures attached"},
            expect_ui_type="supporting_doc_success",
            expect_keywords=["LC-1003", "re-uploaded", "notified"],
        ),
    ],
)

SD214 = SalesDataScenario(
    id=214,
    name="Sales Data — Admin Batch Approve",
    goal="Admin approves LC-2001 and LC-2002 in one confirmation; agent PATCHes each row.",
    tier="sd1",
    tags=["happy", "admin", "approve", "batch"],
    user_email="sales.approver@cenomi.com",
    turns=[
        SalesDataTurn(
            message="Show me submitted sales data for review",
            note="REVIEW_SALES_DATA → admin_sales_data_list",
            expect_ui_type="admin_sales_data_list",
            expect_keywords=["LC-2001", "LC-2002"],
        ),
        SalesDataTurn(
            message="approve LC-2001 and 2002",
            note="Both leases in one admin_confirm card",
            expect_ui_type="admin_confirm",
            expect_keywords=["LC-2001", "LC-2002"],
        ),
        SalesDataTurn(
            message="Confirmed",
            note="Both rows PATCH APPROVED",
            action="confirm_admin_action",
            expect_ui_type="admin_action_success",
            expect_keywords=["LC-2001", "LC-2002", "approved"],
        ),
    ],
)

SD215 = SalesDataScenario(
    id=215,
    name="Sales Data — Supporting Document Happy",
    goal="Tenant responds to NEED_UPLOAD_SUPPORTING_DOCUMENT on LC-1004 with a file, required comment, and PATCH UPLOADED_SUPPORTING_DOCUMENT.",
    tier="sd1",
    tags=["happy", "supporting-doc"],
    turns=[
        SalesDataTurn(
            message="I need to upload a supporting document for lease LC-1004",
            note="UPLOAD_SUPPORTING_DOCUMENT → prompt for supporting document on LC-1004",
            expect_ui_type="supporting_doc_prompt",
            expect_workflow_stage="AWAITING_DOC_UPLOAD",
            expect_keywords=["LC-1004", "supporting document"],
        ),
        SalesDataTurn(
            message="I've attached the POS report",
            note="Cached PDF → PUT /files SD_SUP_DOC → confirmation card",
            action="doc_uploaded",
            pre_upload_doc=True,
            expect_ui_type="supporting_doc_confirm",
            expect_workflow_stage="AWAITING_DOC_CONFIRM",
            expect_keywords=["LC-1004", "supporting document"],
        ),
        SalesDataTurn(
            message="Yes, submit it",
            note="Confirm with required comment → PATCH UPLOADED_SUPPORTING_DOCUMENT",
            action="confirm_supporting_doc",
            corrected_fields={"comment_text": "POS report attached"},
            expect_ui_type="supporting_doc_success",
            expect_keywords=["LC-1004", "supporting document", "notified"],
        ),
    ],
)

SD216 = SalesDataScenario(
    id=216,
    name="Sales Data — Audited Sales Happy",
    goal="Tenant responds to NEED_UPLOAD_AUDITED_SALES on LC-1005 with a file, required comment, and PATCH UPLOADED_AUDITED_SALES.",
    tier="sd1",
    tags=["happy", "audited-sales"],
    turns=[
        SalesDataTurn(
            message="I need to upload audited sales for lease LC-1005",
            note="UPLOAD_SUPPORTING_DOCUMENT → prompt for audited sales on LC-1005",
            expect_ui_type="supporting_doc_prompt",
            expect_workflow_stage="AWAITING_DOC_UPLOAD",
            expect_keywords=["LC-1005", "audited sales"],
        ),
        SalesDataTurn(
            message="I've attached the audited pack",
            note="Cached PDF → PUT /files SD_AUD_DOC → confirmation card",
            action="doc_uploaded",
            pre_upload_doc=True,
            expect_ui_type="supporting_doc_confirm",
            expect_workflow_stage="AWAITING_DOC_CONFIRM",
            expect_keywords=["LC-1005", "audited sales"],
        ),
        SalesDataTurn(
            message="Yes, submit it",
            note="Confirm with required comment → PATCH UPLOADED_AUDITED_SALES",
            action="confirm_supporting_doc",
            corrected_fields={"comment_text": "Audited pack attached"},
            expect_ui_type="supporting_doc_success",
            expect_keywords=["LC-1005", "audited sales", "notified"],
        ),
    ],
)

TIER_SD1 = [SD210, SD211, SD212, SD213, SD214, SD215, SD216]


# =============================================================================
# TIER sd2 — Edge cases (SD220–SD223)
# =============================================================================

SD220 = SalesDataScenario(
    id=220,
    name="Sales Data — Future Date Rejection",
    goal="CSV with a future sales_period_start is rejected at Layer 1 with a clear error.",
    tier="sd2",
    tags=["edge", "future-date", "csv-validation"],
    turns=[
        SalesDataTurn(
            message="I want to upload sales data",
            note="Initial intent",
            expect_ui_type="pending_months_list",
            expect_workflow_stage="AWAITING_PENDING_MONTHS_CHOICE",
        ),
        SalesDataTurn(
            message="Here is my CSV file",
            note="Upload with future date → csv_validation_error UI",
            pre_upload_csv=True,
            csv_rows=_FUTURE_DATE_CSV_ROWS,
            action="csv_uploaded",
            expect_keywords=["future", "past", "period"],
        ),
    ],
)

SD221 = SalesDataScenario(
    id=221,
    name="Sales Data — Zero Amount Rejection",
    goal="CSV with sales_amt=0 is rejected at Layer 1.",
    tier="sd2",
    tags=["edge", "zero-amount", "csv-validation"],
    turns=[
        SalesDataTurn(
            message="I want to upload sales data",
            note="Initial intent",
            expect_ui_type="pending_months_list",
            expect_workflow_stage="AWAITING_PENDING_MONTHS_CHOICE",
        ),
        SalesDataTurn(
            message="Here is my CSV file",
            note="Upload with 0 amount → Layer 1 rejects",
            pre_upload_csv=True,
            csv_rows=_ZERO_AMOUNT_CSV_ROWS,
            action="csv_uploaded",
            expect_keywords=["greater than 0", "invalid", "amount"],
        ),
    ],
)

SD222 = SalesDataScenario(
    id=222,
    name="Sales Data — Cancel Mid-Flow",
    goal="Tenant cancels during manual entry — agent resets cleanly.",
    tier="sd2",
    tags=["edge", "cancel"],
    turns=[
        SalesDataTurn(
            message="I want to enter sales data for lease LC-1001 for August, amount 85000",
            note="Starts manual entry (confirm if all fields extracted)",
            expect_workflow_stage=["AWAITING_MANUAL_CONFIRM", "MANUAL_ENTRY"],
        ),
        SalesDataTurn(
            message="Actually cancel that",
            note="Cancel mid-flow → agent resets, active_agent cleared",
            action="cancel_sales_data",
            expect_keywords=["cancel", "anything else", "start over", "help"],
        ),
    ],
)

SD223 = SalesDataScenario(
    id=223,
    name="Sales Data — Manual Entry Missing Fields",
    goal="Tenant provides partial data — agent asks for missing fields before confirming.",
    tier="sd2",
    tags=["edge", "missing-fields", "manual-entry"],
    turns=[
        SalesDataTurn(
            message="Submit sales data for lease LC-1001",
            note="Lease given but period and amount missing — agent should ask",
            expect_workflow_stage="MANUAL_ENTRY",
            expect_keywords=["period", "amount", "month", "SAR"],
        ),
        SalesDataTurn(
            message="August 2026, SAR 92000",
            note="Provides remaining fields — agent should confirm",
            expect_keywords=["LC-1001", "August", "92"],
        ),
        SalesDataTurn(
            message="Yes submit it",
            note="Confirms → submission",
            action="confirm_manual_entry",
            expect_ui_type="submission_success",
        ),
    ],
)

TIER_SD2 = [SD220, SD221, SD222, SD223]


# =============================================================================
# TIER sd3 — Admin RBAC (SD230–SD231)
# =============================================================================

SD230 = SalesDataScenario(
    id=230,
    name="Sales Data — Tenant Cannot Approve",
    goal="Tenant tries to approve sales data — RBAC denies (no APPROVE_SALES_DATA permission).",
    tier="sd3",
    tags=["rbac", "admin"],
    turns=[
        SalesDataTurn(
            message="I want to approve LC-2001 sales data",
            note="Tenant tries admin action → denied",
            expect_no_keywords=["admin_confirm", "approved"],
            expect_keywords=["not authorised", "not authorized", "cannot", "permission", "doesn't allow", "does not allow", "role"],
        ),
    ],
)

SD231 = SalesDataScenario(
    id=231,
    name="Sales Data — Admin Cannot Upload",
    goal="Admin (SALES_DATA_APPROVER) tries to upload sales data — denied (tenant-only intent).",
    tier="sd3",
    tags=["rbac", "admin"],
    user_email="sales.approver@cenomi.com",
    turns=[
        SalesDataTurn(
            message="I want to upload my sales data CSV for August",
            note="SALES_DATA_APPROVER lacks UPLOAD_SALES_DATA — should be denied",
            expect_keywords=["not authorised", "not authorized", "cannot", "permission", "doesn't allow", "does not allow", "role"],
        ),
    ],
)

SD232 = SalesDataScenario(
    id=232,
    name="Sales Data — Tenant Pending Question Is Own Status Not Admin List",
    goal="Tenant asking 'what data are pending' gets tenant-scoped status_summary, not the admin review list.",
    tier="sd3",
    tags=["rbac", "status"],
    turns=[
        SalesDataTurn(
            message="What data are pending ?",
            note="Tenant → CHECK_SALES_DATA_STATUS (own leases only)",
            expect_ui_type="status_summary",
            expect_keywords=["LC-1001", "pending"],
            expect_no_keywords=["admin_confirm", "awaiting review"],
        ),
    ],
)

SD233 = SalesDataScenario(
    id=233,
    name="Sales Data — Tenant Cannot Review Admin Queue",
    goal="Tenant asking to review submitted sales is RBAC-denied (no REVIEW_SALES_DATA).",
    tier="sd3",
    tags=["rbac", "admin"],
    turns=[
        SalesDataTurn(
            message="Show me submitted sales data for review",
            note="REVIEW_SALES_DATA is admin-only — tenant must be denied",
            expect_keywords=["not authorised", "not authorized", "cannot", "permission", "doesn't allow", "does not allow", "role", "does not permit"],
            expect_no_keywords=["LC-2001"],
        ),
    ],
)

SD234 = SalesDataScenario(
    id=234,
    name="Sales Data — Admin Ask Upload Supporting Document",
    goal="Sales Data team requests a supporting document on LC-2001 (portal Ask upload).",
    tier="sd3",
    tags=["admin", "ask-upload", "supporting-doc"],
    user_email="sales.approver@cenomi.com",
    turns=[
        SalesDataTurn(
            message="Show me submitted sales data for review",
            note="REVIEW_SALES_DATA → admin_sales_data_list",
            expect_ui_type="admin_sales_data_list",
            expect_keywords=["LC-2001"],
        ),
        SalesDataTurn(
            message="request supporting document for LC-2001",
            note="Ask upload — Supporting document confirm card",
            expect_ui_type="admin_confirm",
            expect_keywords=["LC-2001", "supporting document"],
        ),
        SalesDataTurn(
            message="Confirmed",
            note="PATCH NEED_UPLOAD_SUPPORTING_DOCUMENT",
            action="confirm_admin_action",
            expect_ui_type="admin_action_success",
            expect_keywords=["LC-2001", "supporting document"],
        ),
    ],
)

SD235 = SalesDataScenario(
    id=235,
    name="Sales Data — Admin Ask Upload Audited Sales",
    goal="Sales Data team requests audited sales on LC-2002 (portal Ask upload).",
    tier="sd3",
    tags=["admin", "ask-upload", "audited-sales"],
    user_email="sales.approver@cenomi.com",
    turns=[
        SalesDataTurn(
            message="Show me submitted sales data for review",
            note="REVIEW_SALES_DATA → admin_sales_data_list",
            expect_ui_type="admin_sales_data_list",
            expect_keywords=["LC-2002"],
        ),
        SalesDataTurn(
            message="request audited sales for LC-2002",
            note="Ask upload — Audited sales data confirm card",
            expect_ui_type="admin_confirm",
            expect_keywords=["LC-2002", "audited"],
        ),
        SalesDataTurn(
            message="Confirmed",
            note="PATCH NEED_UPLOAD_AUDITED_SALES",
            action="confirm_admin_action",
            expect_ui_type="admin_action_success",
            expect_keywords=["LC-2002", "audited"],
        ),
    ],
)

SD236 = SalesDataScenario(
    id=236,
    name="Sales Data — Admin Notification Open Scopes to One Lease",
    goal=(
        "Admin opens a SALES_DATA_SUBMITTED notification for LC-2001 — the "
        "review list must scope to that lease, not show the whole backlog "
        "(the recurring 'clicked one notification, saw everything' bug)."
    ),
    tier="sd3",
    tags=["admin", "notification", "scoping"],
    user_email="sales.approver@cenomi.com",
    turns=[
        SalesDataTurn(
            message="",
            note="Open SALES_DATA_SUBMITTED notification for LC-2001 → scoped admin_sales_data_list",
            active_notification_id={
                "id": "LC-2001",
                "key": "SALES_DATA_SUBMITTED",
                "sub_category": "SALES_DATA",
                "message": (
                    "Please review the sales data for Centrepoint-Riyadh Park "
                    "corresponding to month July"
                ),
            },
            expect_ui_type="admin_sales_data_list",
            expect_keywords=["LC-2001"],
            expect_no_keywords=["LC-2002"],
        ),
    ],
)

SD237 = SalesDataScenario(
    id=237,
    name="Sales Data — NEED_REUPLOAD Notification Opens Reupload Prompt",
    goal=(
        "Tenant opens their own SALES_DATA_NEED_REUPLOAD notification for "
        "LC-1003 — must land on the reupload/document prompt, not "
        "'nothing pending' (NEED_REUPLOAD has no PENDING row, so routing "
        "this through the pending-months gate silently found nothing)."
    ),
    tier="sd3",
    tags=["tenant", "notification", "reupload", "routing"],
    turns=[
        SalesDataTurn(
            message="",
            note="Open SALES_DATA_NEED_REUPLOAD notification for LC-1003 → doc/reupload prompt, not 'nothing pending'",
            active_notification_id={
                "id": "LC-1003",
                "key": "SALES_DATA_NEED_REUPLOAD",
                "sub_category": "SALES_DATA",
                "message": "Please re-submit sales data for LC-1003 as of month August.",
            },
            expect_ui_type="supporting_doc_prompt",
            expect_workflow_stage="AWAITING_DOC_UPLOAD",
            expect_keywords=["LC-1003"],
            expect_no_keywords=["no pending", "nothing pending", "already been submitted"],
        ),
    ],
)

TIER_SD3 = [SD230, SD231, SD232, SD233, SD234, SD235, SD236, SD237]

# ---------------------------------------------------------------------------
# Full flat registry for reference
# ---------------------------------------------------------------------------

ALL_SD_SCENARIOS: list[SalesDataScenario] = [
    *TIER_SD0,
    *TIER_SD1,
    *TIER_SD2,
    *TIER_SD3,
]
