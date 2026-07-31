"""FM_REVIEW, RDD_REVIEW, full-lifecycle, and fitout-confirm evaluation scenarios.

Extends the 33 CREATE_SR scenarios in scenarios.py with:
  - Scenarios 34–39  → FM_REVIEW phase (FM Manager / Operations)
  - Scenarios 40–45  → RDD_REVIEW phase (DD Engineer / RDD PM)
  - Scenarios 46–47  → Full 3-phase lifecycle (CREATE → FM → RDD → COMPLETE)
  - L150–L158        → HANDED_OVER / Path B lifecycle scenarios (Tier 7)
  - L170–L182        → Fitout Confirmation SR (FOS/END/TRADING) lifecycle (Tier 9)

Architecture
------------
Each ``LifecycleScenario`` has one or more ``PhaseConfig`` objects.
A phase specifies:
  - The user credentials to authenticate with (seeded test users)
  - Whether to open an existing SR at phase entry (FM/RDD phases)
  - Whether to upload a mock document before running turns
  - The list of ``Turn`` objects for that phase

The runner (``run_lifecycle_eval.py``) executes phases sequentially,
passing the SR ID forward between phases automatically.

Role → seeded user matrix
--------------------------
  MALL_MANAGER  → aisha@cenomi.com   / test1234
  FM_MANAGER    → khalid@cenomi.com  / test1234
  DD_ENGINEER   → sara@cenomi.com    / test1234
  OPERATIONS    → omar@cenomi.com    / test1234
  ADMIN         → admin@cenomi.com   / test1234
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional

from tests.eval.scenarios import Turn

# ---------------------------------------------------------------------------
# Dynamic date helpers (mirroring scenarios.py — keeps lifecycle dates fresh)
# ---------------------------------------------------------------------------

_TODAY = datetime.date.today()


def _d(days: int) -> str:
    """Return ISO-format date string N days from today."""
    return (_TODAY + datetime.timedelta(days=days)).isoformat()


# ─── Data models ──────────────────────────────────────────────────────────────

@dataclass
class PhaseConfig:
    """Configuration for one phase of a multi-phase lifecycle scenario."""

    # Who runs this phase
    user_email: str
    user_password: str
    role: str               # display label only
    stage: str              # expected workflow stage at entry (informational)

    # Turns to execute in this phase
    turns: list[Turn]

    # If True, the first turn passes the SR ID captured from the previous phase
    # so the agent enters FM_REVIEW / RDD_REVIEW for the correct SR.
    open_existing_sr: bool = False

    # If True, upload a mock document before running turns (required by FM/RDD
    # validation: at least 1 document must be uploaded before save/approve).
    upload_mock_document: bool = False
    document_type: str = "SR_HANDOVER_CHECKLIST"   # default FM doc type


@dataclass
class LifecycleScenario:
    """A multi-phase evaluation scenario spanning two or three workflow stages."""

    id: int
    name: str
    goal: str
    phases: list[PhaseConfig]
    enabled: bool = True
    tags: list[str] = field(default_factory=list)


# ─── Credential constants ──────────────────────────────────────────────────────

_MALL  = ("aisha@cenomi.com",  "test1234", "MALL_MANAGER")
_FM    = ("khalid@cenomi.com", "test1234", "FM_MANAGER")
_OPS   = ("omar@cenomi.com",   "test1234", "OPERATIONS")
_DD    = ("sara@cenomi.com",   "test1234", "DD_ENGINEER")


# ─── Shared CREATE_SR setup phase ─────────────────────────────────────────────
#
# Each FM or RDD scenario needs a freshly-submitted SR to work on.
# This helper returns a standard CREATE_SR phase that produces an SR ID
# for the subsequent FM / RDD phase.

def _create_fos_sr_phase() -> PhaseConfig:
    """Return a CREATE_SR setup phase that submits a FOS SR and captures its sr_id.

    Used as the first phase of APPROVE_SR lifecycle scenarios so the approver
    phase has a real sr_id to open.
    """
    return PhaseConfig(
        user_email=_DD[0], user_password=_DD[1], role=_DD[2],
        stage="CREATE_SR",
        turns=[
            Turn(
                message=f"Confirm fitout start for t0105712. Yes, fitout has started. Date: {_d(-20)}",
                note="Create FOS SR — yes branch, all fields in one message; confirmation card expected",
            ),
            Turn(
                message="Confirm",
                action="confirm",
                note="Submit FOS SR → SR_CREATED; sr_id captured for APPROVE phase",
                expect_workflow_stage="SR_CREATED",
                expect_keywords=["submitted", "reference"],
            ),
        ],
    )


def _create_end_sr_phase() -> PhaseConfig:
    """Return a CREATE_SR setup phase that submits an END SR and captures its sr_id."""
    return PhaseConfig(
        user_email=_DD[0], user_password=_DD[1], role=_DD[2],
        stage="CREATE_SR",
        turns=[
            Turn(
                message=f"Confirm fitout end for t0105712. Yes, fitout is complete. Date: {_d(-15)}",
                note="Create END SR — yes branch, all fields in one message; confirmation card expected",
            ),
            Turn(
                message="Confirm",
                action="confirm",
                note="Submit END SR → SR_CREATED; sr_id captured for APPROVE phase",
                expect_workflow_stage="SR_CREATED",
                expect_keywords=["submitted", "reference"],
            ),
        ],
    )


def _create_trading_sr_phase() -> PhaseConfig:
    """Return a CREATE_SR setup phase that submits a TRADING SR and captures its sr_id."""
    return PhaseConfig(
        user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
        stage="CREATE_SR",
        turns=[
            Turn(
                message=f"Confirm trading for t0105712. Yes, trading has started. Date: {_d(-10)}",
                note="Create TRADING SR — yes branch, all fields in one message; confirmation card expected",
            ),
            Turn(
                message="Confirm",
                action="confirm",
                note="Submit TRADING SR → SR_CREATED; sr_id captured for APPROVE phase",
                expect_workflow_stage="SR_CREATED",
                expect_keywords=["submitted", "reference"],
            ),
        ],
    )


def _create_sr_phase(lease_code: str = "t0105712") -> PhaseConfig:
    """Return a standard CREATE_SR setup phase for Under Armour Jawharat Jeddah."""
    return PhaseConfig(
        user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
        stage="CREATE_SR",
        turns=[
            Turn(
                message=(
                    f"Create a handover service request for lease {lease_code}. "
                    f"Description: Standard fit-out inspection. "
                    f"Start {_d(15)}, end {_d(19)}, done by FM Manager. "
                    "No inspection time. No notes. No comments."
                ),
                note="All fields in one message (including optional inspectionTime, notes, comments); confirmation card expected",
            ),
            Turn(
                message="Confirm",
                note="Submit SR — sr_id returned",
                action="confirm",
                expect_workflow_stage="SR_CREATED",
                expect_keywords=["submitted", "reference", "successfully", "created"],
            ),
        ],
    )


# ─── FM_REVIEW setup phase ─────────────────────────────────────────────────────
#
# Runs FM_REVIEW and approval, leaving the SR in RDD_REVIEW.
# Used as setup for RDD scenarios.

def _fm_approve_phase() -> PhaseConfig:
    """Return an FM_REVIEW phase that provides dates and approves."""
    return PhaseConfig(
        user_email=_FM[0], user_password=_FM[1], role=_FM[2],
        stage="FM_REVIEW",
        open_existing_sr=True,
        upload_mock_document=True,
        document_type="SR_HANDOVER_CHECKLIST",
        turns=[
            Turn(
                message=f"Unit readiness date is {_d(25)}. No comments.",
                note="Provide unit_readiness_date + comments in one message; expected_handover_date auto-computed",
                expect_workflow_stage="FM_REVIEW",
            ),
            Turn(
                message="",
                note="Approve FM review",
                action="approve_fm_review",
                expect_workflow_stage="FM_REVIEW",
            ),
            Turn(
                message="",
                note="Confirm FM approval",
                action="confirm_fm_approve",
                expect_keywords=["approved", "fm", "review", "handover"],
            ),
        ],
    )


# ─── Scenarios 34–39: FM_REVIEW ───────────────────────────────────────────────

_S34 = LifecycleScenario(
    id=134,
    name="FM Review — Happy Path (date + approve)",
    goal="FM Manager opens SR, provides unit readiness date, uploads document, and approves.",
    tags=["fm-review", "happy-path"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="SR_HANDOVER_CHECKLIST",
            turns=[
                Turn(
                    message="Unit readiness date is 10th August 2026.",
                    note="Provide unit_readiness_date; bot acknowledges",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Approve FM review",
                    action="approve_fm_review",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Confirm FM approval → fm_status=APPROVED",
                    action="confirm_fm_approve",
                    expect_keywords=["approved", "fm", "review"],
                ),
            ],
        ),
    ],
)


_S35 = LifecycleScenario(
    id=135,
    name="FM Review — Save Progress then Approve",
    goal="FM Manager saves progress first, then approves in a separate action.",
    tags=["fm-review", "save-progress"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="SR_HANDOVER_SITE_SURVEY",
            turns=[
                Turn(
                    message="Unit readiness date is 2026-08-10.",
                    note="Provide unit_readiness_date",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Save progress (IN_PROCESS status)",
                    action="save_fm_progress",
                    expect_workflow_stage="FM_REVIEW",
                    expect_keywords=["saved", "progress", "save", "noted"],
                ),
                Turn(
                    message="",
                    note="Approve FM review after save",
                    action="approve_fm_review",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Confirm approval",
                    action="confirm_fm_approve",
                    expect_keywords=["approved", "review"],
                ),
            ],
        ),
    ],
)


_S36 = LifecycleScenario(
    id=136,
    name="FM Review — Natural Language Date",
    goal="FM Manager provides unit readiness date in natural language ('fifteenth of August 2026').",
    tags=["fm-review", "date-parsing", "natural-language"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            turns=[
                Turn(
                    message="Unit readiness date is the fifteenth of August 2026.",
                    note="Natural language date; LLM must parse to 2026-08-15",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Approve FM review",
                    action="approve_fm_review",
                ),
                Turn(
                    message="",
                    note="Confirm",
                    action="confirm_fm_approve",
                    expect_keywords=["approved", "review"],
                ),
            ],
        ),
    ],
)


_S37 = LifecycleScenario(
    id=137,
    name="FM Review — Date + Comments Together",
    goal="FM Manager provides unit_readiness_date and comments in a single message.",
    tags=["fm-review", "multi-field"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            turns=[
                Turn(
                    message=(
                        "Unit readiness date is 10th August 2026. "
                        "The unit has been cleaned and is ready for handover. No further comments."
                    ),
                    note="Date + comments extracted simultaneously",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Approve with one action",
                    action="approve_fm_review",
                ),
                Turn(
                    message="",
                    note="Confirm",
                    action="confirm_fm_approve",
                    expect_keywords=["approved"],
                ),
            ],
        ),
    ],
)


_S38 = LifecycleScenario(
    id=138,
    name="FM Review — RBAC: DD_ENGINEER Cannot FM Approve",
    goal="DD Engineer opening an FM_REVIEW SR must be denied FM approval actions.",
    tags=["fm-review", "rbac", "security"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            turns=[
                Turn(
                    message="I want to approve this FM review.",
                    note="DD_ENGINEER attempting FM action must be denied",
                    expect_keywords=["not authorised", "not authorized", "permission", "cannot", "only", "fm", "role"],
                ),
            ],
        ),
    ],
)


_S39 = LifecycleScenario(
    id=139,
    name="FM Review — Operations Role (Same Permissions as FM Manager)",
    goal="OPERATIONS role can also review and approve at FM_REVIEW stage.",
    tags=["fm-review", "rbac", "operations"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_OPS[0], user_password=_OPS[1], role=_OPS[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="SR_HANDOVER_OTHER",
            turns=[
                Turn(
                    message="Unit readiness date is 2026-08-12.",
                    note="Operations role provides date — should work same as FM_MANAGER",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Approve FM review as Operations",
                    action="approve_fm_review",
                ),
                Turn(
                    message="",
                    note="Confirm",
                    action="confirm_fm_approve",
                    expect_keywords=["approved"],
                ),
            ],
        ),
    ],
)


# ─── Scenarios 40–45: RDD_REVIEW ──────────────────────────────────────────────

_S40 = LifecycleScenario(
    id=140,
    name="RDD Review — Happy Path (all fields → submit → final approve)",
    goal=(
        "DD Engineer opens SR in RDD_REVIEW, provides all fields in one message, "
        "submits report, then gives final approval → SR_COMPLETED."
    ),
    tags=["rdd-review", "happy-path"],
    phases=[
        _create_sr_phase(),
        _fm_approve_phase(),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="DR_SR_HANDOVER_REPORT",
            turns=[
                Turn(
                    message=(
                        "Guidelines link is https://cenomi.example.com/guidelines. "
                        "Document type: Handover Meeting Report. Document status is approved. "
                        "Actual handover date is 20th August 2026. "
                        "Fitout starts 22nd August 2026, ends 5th September 2026. "
                        "No comments."
                    ),
                    note="All RDD fields extracted including doc type; trading_date auto-computed",
                    expect_workflow_stage="RDD_REVIEW",
                ),
                Turn(
                    message="",
                    note="Submit RDD report",
                    action="submit_rdd_report",
                    expect_workflow_stage="RDD_REVIEW",
                ),
                Turn(
                    message="",
                    note="Confirm report submission → REPORT_SUBMITTED",
                    action="confirm_rdd_submit",
                    expect_keywords=["submitted", "report"],
                ),
                Turn(
                    message="",
                    note="Final approval",
                    action="approve_rdd_final",
                ),
                Turn(
                    message="",
                    note="Confirm final approval → SR_COMPLETED",
                    action="confirm_rdd_final_approve",
                    expect_workflow_stage="SR_COMPLETED",
                    expect_keywords=["completed", "final", "approved", "handover"],
                ),
            ],
        ),
    ],
)


_S41 = LifecycleScenario(
    id=141,
    name="RDD Review — Field Collection Turn by Turn (correct order)",
    goal=(
        "DD Engineer provides each RDD field separately across multiple turns "
        "in the correct order: guideLineLink → doc type chip → status chip → dates → comments."
    ),
    tags=["rdd-review", "field-collection", "field-ordering"],
    phases=[
        _create_sr_phase(),
        _fm_approve_phase(),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="DR_SR_HANDOVER_REPORT",
            turns=[
                Turn(
                    message="The guidelines are at https://cenomi.example.com/rdd",
                    note="guideLineLink accepted; bot must ask for document type chips NEXT — not any date field",
                    expect_workflow_stage="RDD_REVIEW",
                    expect_keywords=["document", "type", "meeting", "report", "handover", "rejected", "other"],
                ),
                Turn(
                    message="",
                    note="Select Handover Meeting Report via chip — bot must ask for document_status chips next",
                    action="select_rdd_doc_meeting",
                    expect_keywords=["approve", "approved", "status", "changes", "decision"],
                ),
                Turn(
                    message="",
                    note="Select Approved status via chip — bot now asks for actual_handover_date (first date)",
                    action="select_rdd_status_approved",
                    expect_keywords=["actual", "handover", "date", "when", "completed"],
                ),
                Turn(
                    message="Actual handover date is 20 August 2026.",
                    note="actual_handover_date accepted; bot asks for fitout_start_date",
                ),
                Turn(
                    message="Fitout starts on 22nd August.",
                    note="fitout_start_date accepted; bot asks for fitout_end_date",
                ),
                Turn(
                    message="Fitout ends 5th September 2026.",
                    note="fitout_end_date accepted; trading_date auto-computed; bot asks for comments",
                ),
                Turn(
                    message="No comments.",
                    note="Comments accepted (empty optional); Submit chip must now appear — all fields done + Meeting Report uploaded",
                ),
                Turn(
                    message="",
                    note="Submit RDD report",
                    action="submit_rdd_report",
                ),
                Turn(
                    message="",
                    note="Confirm submission → REPORT_SUBMITTED; workflow_stage stays RDD_REVIEW",
                    action="confirm_rdd_submit",
                    expect_keywords=["submitted"],
                ),
                Turn(
                    message="",
                    note="Final approve",
                    action="approve_rdd_final",
                ),
                Turn(
                    message="",
                    note="Confirm final approval → SR_COMPLETED",
                    action="confirm_rdd_final_approve",
                    expect_workflow_stage="SR_COMPLETED",
                ),
            ],
        ),
    ],
)


_S42 = LifecycleScenario(
    id=142,
    name="RDD Review — Date Chain Validation (fitout_end < fitout_start blocked)",
    goal="Providing fitout_end_date before fitout_start_date triggers a validation error that must be corrected.",
    tags=["rdd-review", "date-validation", "error-handling"],
    phases=[
        _create_sr_phase(),
        _fm_approve_phase(),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="DR_SR_HANDOVER_REPORT",
            turns=[
                Turn(
                    message=(
                        "Guidelines https://cenomi.example.com/rdd. "
                        "Document type: Handover Meeting Report. Document status approved. "
                        "Actual handover 20 August 2026. "
                        "Fitout starts September 5 and ends August 22 2026. "
                        "No comments."
                    ),
                    note="fitout_end < fitout_start — must block and ask to correct",
                    expect_no_submit=True,
                    expect_keywords=["fitout", "start", "end", "date", "after", "before", "invalid", "order"],
                ),
                Turn(
                    message="Fitout starts 22 August and ends 5 September 2026.",
                    note="Correct date order; proceed",
                    expect_workflow_stage="RDD_REVIEW",
                ),
                Turn(
                    message="",
                    note="Submit report",
                    action="submit_rdd_report",
                ),
                Turn(
                    message="",
                    note="Confirm",
                    action="confirm_rdd_submit",
                    expect_keywords=["submitted"],
                ),
                Turn(
                    message="",
                    note="Final approve",
                    action="approve_rdd_final",
                ),
                Turn(
                    message="",
                    note="Confirm final approval",
                    action="confirm_rdd_final_approve",
                    # SR_COMPLETED may appear on this turn or the next sync turn;
                    # the key assertion for S42 is expect_no_submit=True at T1.
                    expect_keywords=["completed", "approved", "final", "handover"],
                ),
            ],
        ),
    ],
)


_S43 = LifecycleScenario(
    id=143,
    name="RDD Review — Approve With Changes (APPROVED_WITH_CHANGES)",
    goal="DD Engineer sets document_status to APPROVED_WITH_CHANGES and completes the review.",
    tags=["rdd-review", "document-status"],
    phases=[
        _create_sr_phase(),
        _fm_approve_phase(),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="DR_SR_HANDOVER_REPORT",
            turns=[
                Turn(
                    message=(
                        "Guidelines at https://cenomi.example.com/rdd. "
                        "Document type: Handover Meeting Report. "
                        "Document status is approve with changes. "
                        "Actual handover 20 August 2026. "
                        "Fitout 22 August to 5 September 2026. "
                        "No comments."
                    ),
                    note="document_status=APPROVED_WITH_CHANGES",
                    expect_workflow_stage="RDD_REVIEW",
                ),
                Turn(
                    message="",
                    note="Submit",
                    action="submit_rdd_report",
                ),
                Turn(
                    message="",
                    note="Confirm",
                    action="confirm_rdd_submit",
                    expect_keywords=["submitted"],
                ),
                Turn(
                    message="",
                    note="Final approve",
                    action="approve_rdd_final",
                ),
                Turn(
                    message="",
                    note="Confirm → SR_COMPLETED",
                    action="confirm_rdd_final_approve",
                    expect_workflow_stage="SR_COMPLETED",
                ),
            ],
        ),
    ],
)


_S44 = LifecycleScenario(
    id=144,
    name="RDD Review — Natural Language Dates",
    goal="All RDD dates provided in human-friendly format are correctly parsed.",
    tags=["rdd-review", "date-parsing", "natural-language"],
    phases=[
        _create_sr_phase(),
        _fm_approve_phase(),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="DR_SR_HANDOVER_REPORT",
            turns=[
                Turn(
                    message=(
                        "Guidelines https://cenomi.example.com/rdd. "
                        "Document type: Handover Meeting Report. Document status approved. "
                        "Actual handover on the twentieth of August. "
                        "Fitout from the twenty-second of August until the fifth of September. "
                        "No comments."
                    ),
                    note="Natural language dates; LLM must parse all correctly",
                    expect_workflow_stage="RDD_REVIEW",
                ),
                Turn(
                    message="",
                    note="Submit",
                    action="submit_rdd_report",
                ),
                Turn(
                    message="",
                    note="Confirm",
                    action="confirm_rdd_submit",
                    expect_keywords=["submitted"],
                ),
                Turn(
                    message="",
                    note="Final approve",
                    action="approve_rdd_final",
                ),
                Turn(
                    message="",
                    note="Confirm → SR_COMPLETED",
                    action="confirm_rdd_final_approve",
                    expect_workflow_stage="SR_COMPLETED",
                ),
            ],
        ),
    ],
)


_S45 = LifecycleScenario(
    id=145,
    name="RDD Review — RBAC: FM_MANAGER Cannot RDD Approve",
    goal="FM Manager attempting RDD actions on an RDD_REVIEW SR must be denied.",
    tags=["rdd-review", "rbac", "security"],
    phases=[
        _create_sr_phase(),
        _fm_approve_phase(),
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            turns=[
                Turn(
                    message="I want to do the final approval for this RDD review.",
                    note="FM_MANAGER attempting RDD action must be denied",
                    expect_keywords=["not authorised", "not authorized", "permission", "cannot", "rdd", "role", "only"],
                ),
            ],
        ),
    ],
)


# ─── Scenarios 46–47: Full 3-phase lifecycle ──────────────────────────────────

_S46 = LifecycleScenario(
    id=146,
    name="Full Lifecycle — CREATE → FM_REVIEW → RDD_REVIEW → SR_COMPLETED",
    goal=(
        "Complete end-to-end RDD handover lifecycle: Mall Manager creates SR, "
        "FM Manager approves, DD Engineer submits report and gives final approval. "
        "Final state must be SR_COMPLETED."
    ),
    tags=["full-lifecycle", "happy-path", "core"],
    phases=[
        # Phase 1: Mall Manager creates SR
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message=(
                        "Create a handover service request for lease t0105712. "
                        "Description: Full lifecycle end-to-end test. "
                        "Start 2026-09-01, end 2026-09-05, done by FM Manager. No notes."
                    ),
                    note="All fields in one shot; confirmation card expected",
                ),
                Turn(
                    message="Confirm",
                    note="Submit SR",
                    action="confirm",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference", "created"],
                ),
            ],
        ),
        # Phase 2: FM Manager reviews
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="SR_HANDOVER_CHECKLIST",
            turns=[
                Turn(
                    message="Unit readiness date is 15th September 2026.",
                    note="Provide date; expected_handover_date auto-computed",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Approve",
                    action="approve_fm_review",
                ),
                Turn(
                    message="",
                    note="Confirm approval",
                    action="confirm_fm_approve",
                    expect_keywords=["approved"],
                ),
            ],
        ),
        # Phase 3: DD Engineer final approval
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="DR_SR_HANDOVER_REPORT",
            turns=[
                Turn(
                    message=(
                        "Guidelines at https://cenomi.example.com/handover-2026. "
                        "Document type: Handover Meeting Report. Document status approved. "
                        "Actual handover 25 September 2026. "
                        "Fitout from 28 September to 15 October 2026. "
                        "No comments."
                    ),
                    note="All RDD fields in one message",
                    expect_workflow_stage="RDD_REVIEW",
                ),
                Turn(
                    message="",
                    note="Submit RDD report",
                    action="submit_rdd_report",
                ),
                Turn(
                    message="",
                    note="Confirm submission",
                    action="confirm_rdd_submit",
                    expect_keywords=["submitted"],
                ),
                Turn(
                    message="",
                    note="Final approval",
                    action="approve_rdd_final",
                ),
                Turn(
                    message="",
                    note="Confirm → SR_COMPLETED — lifecycle done",
                    action="confirm_rdd_final_approve",
                    expect_workflow_stage="SR_COMPLETED",
                    expect_keywords=["completed", "approved", "handover", "final"],
                ),
            ],
        ),
    ],
)


_S47 = LifecycleScenario(
    id=147,
    name="Full Lifecycle — With Corrections at Each Stage",
    goal=(
        "Mall Manager corrects the end date before submission; FM Manager corrects the "
        "unit_readiness_date after saving; RDD PM corrects a fitout date. "
        "All corrections are accepted and the lifecycle reaches SR_COMPLETED."
    ),
    tags=["full-lifecycle", "correction", "state-management"],
    phases=[
        # Phase 1: Create SR with inline correction
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message=(
                        "Handover request for t0208831 (Nike Riyadh Park). "
                        "Description: Lifecycle with corrections test. "
                        "Start 2026-09-01, end 2026-09-03, FM Manager. No notes."
                    ),
                    note="Initial submission — confirmation card expected",
                ),
                Turn(
                    message="Confirm",
                    note="Submit with corrected end date via corrected_fields",
                    action="confirm",
                    corrected_fields={"endDate": "2026-09-05"},
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference"],
                ),
            ],
        ),
        # Phase 2: FM with date correction
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            turns=[
                Turn(
                    message="Unit readiness date is 10th September 2026.",
                    note="Initial date provided",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Save progress with corrected unit_readiness_date",
                    action="save_fm_progress",
                    corrected_fields={"unit_readiness_date": "2026-09-12"},
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Confirm save",
                    action="confirm_fm_save",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Approve FM review",
                    action="approve_fm_review",
                ),
                Turn(
                    message="",
                    note="Confirm approval",
                    action="confirm_fm_approve",
                    expect_keywords=["approved"],
                ),
            ],
        ),
        # Phase 3: RDD with corrected fitout dates
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="DR_SR_HANDOVER_REPORT",
            turns=[
                Turn(
                    message=(
                        "Guidelines https://cenomi.example.com/lifecycle-corrections. "
                        "Document type: Handover Meeting Report. Document status approved. "
                        "Actual handover 20 September 2026. "
                        "Fitout 25 September to 15 October 2026. "
                        "No comments."
                    ),
                    note="All RDD fields",
                    expect_workflow_stage="RDD_REVIEW",
                ),
                Turn(
                    message="",
                    note="Submit report with corrected fitout_end_date",
                    action="submit_rdd_report",
                    corrected_fields={"fitout_end_date": "2026-10-18"},
                ),
                Turn(
                    message="",
                    note="Confirm",
                    action="confirm_rdd_submit",
                    expect_keywords=["submitted"],
                ),
                Turn(
                    message="",
                    note="Final approve",
                    action="approve_rdd_final",
                ),
                Turn(
                    message="",
                    note="Confirm → SR_COMPLETED",
                    action="confirm_rdd_final_approve",
                    expect_workflow_stage="SR_COMPLETED",
                    expect_keywords=["completed"],
                ),
            ],
        ),
    ],
)


# ─── Scenarios 48–49: MM optional upload gate (CREATE_SR) ────────────────────
#
# These scenarios test the MM optional upload offer introduced in staging
# (handover_entry_node._handle_mm_optional_upload_gate).  The gate activates
# when collected_data contains mm_optional_upload_status == "awaiting_choice"
# after field extraction detects a Mall Manager context.

_S48 = LifecycleScenario(
    id=148,
    name="CREATE_SR — MM Optional Upload Accepted",
    goal=(
        "Mall Manager is offered the optional MM document upload after all required "
        "fields are filled, accepts it, uploads a document, then continues to submit."
    ),
    tags=["create-sr", "mm-upload", "optional-upload", "happy-path"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message=(
                        "Create a handover request for lease t0105712. "
                        "Description: MM upload test — happy path. "
                        "Start 2026-09-01, end 2026-09-10, FM Manager. No notes."
                    ),
                    note="All fields provided; confirmation card expected",
                ),
                Turn(
                    message="",
                    note="Accept the optional MM upload offer",
                    action="accept_optional_upload",
                    expect_keywords=["upload", "document"],
                ),
                Turn(
                    message="",
                    note="Continue after uploading — mark upload done",
                    action="continue_optional_upload",
                    expect_keywords=["continue", "next", "proceed", "upload"],
                ),
                Turn(
                    message="Confirm",
                    note="Submit SR",
                    action="confirm",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference", "successfully", "created"],
                ),
            ],
        ),
    ],
)


_S49 = LifecycleScenario(
    id=149,
    name="CREATE_SR — MM Optional Upload Skipped",
    goal=(
        "Mall Manager is offered the optional MM document upload, declines it, "
        "and the SR is submitted without any extra document."
    ),
    tags=["create-sr", "mm-upload", "optional-upload", "skip"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message=(
                        "Create a handover request for lease t0208831. "
                        "Description: MM upload test — skip path. "
                        "Start 2026-09-05, end 2026-09-15, FM Manager. No notes."
                    ),
                    note="All fields provided; optional upload offer shown",
                ),
                Turn(
                    message="",
                    note="Skip the optional MM upload",
                    action="skip_optional_upload",
                    expect_keywords=["skip", "continue", "proceed", "without"],
                ),
                Turn(
                    message="Confirm",
                    note="Submit SR immediately after skipping",
                    action="confirm",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference", "successfully", "created"],
                ),
            ],
        ),
    ],
)


# =============================================================================
# Scenarios 50–72 — State management & issue battle tests (agent_ui_fixes.md)
# =============================================================================

# ─── State contamination / session isolation (S50–S55) ────────────────────────

_S50 = LifecycleScenario(
    id=150,
    name="Stale sr_id from previous session cleared on new CREATE_SR request",
    goal=(
        "After one SR is created and workflow ends, a new CREATE_SR intent in the same session "
        "must start with a clean sr_id — agent must not route to FM/RDD for the old SR"
    ),
    tags=["state-contamination", "sr_id-leak", "session-isolation"],
    phases=[
        # Phase 1: create first SR
        _create_sr_phase(),
        # Phase 2: start a new request in the same session (same user)
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="I want to raise another handover request",
                    note="New CREATE_SR intent; old sr_id must NOT cause FM_REVIEW routing",
                    expect_workflow_stage="CREATE_SR",
                    expect_keywords=["lease", "code", "brand", "mall"],
                ),
                Turn(
                    message="t0419977",
                    note="Zara Dubai resolved; must be a fresh SR context without Under Armour data",
                ),
                Turn(
                    message="Zara pre-opening inspection",
                    note="Description accepted; continues as new SR",
                ),
                Turn(
                    message=_d(75),
                    note="Start date accepted",
                ),
                Turn(
                    message=_d(77),
                    note="End date accepted",
                ),
                Turn(
                    message="FM Manager",
                    note="Inspector accepted",
                ),
                Turn(
                    message="No notes",
                    note="Notes accepted",
                ),
                Turn(
                    message="No comments",
                    note="Comments accepted; confirmation card shown — must show Zara, not Under Armour",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    note="New SR submitted; must have a different sr_id from first SR",
                    action="confirm",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference", "successfully"],
                ),
            ],
        ),
    ],
)


_S51 = LifecycleScenario(
    id=151,
    name="Stale collected_data cleared after cancel",
    goal=(
        "After successful SR cancellation, all fields in collected_data must be empty "
        "so the next request starts from a completely clean slate"
    ),
    tags=["state-contamination", "cancel-cleanup", "issue-4"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="",
                    note="Cancel SR action after creation",
                    action="cancel_sr",
                    expect_keywords=["cancel", "sure", "confirm"],
                ),
                Turn(
                    message="",
                    note="Confirm cancellation",
                    action="confirm_cancel_sr",
                    expect_keywords=["cancelled"],
                    expect_no_active_agent=True,
                ),
                Turn(
                    message="I want to create a new handover request",
                    note="New intent; collected_data must be empty — no Under Armour fields",
                    expect_keywords=["lease", "code", "brand", "mall"],
                ),
            ],
        ),
    ],
)


_S52 = LifecycleScenario(
    id=152,
    name="Role isolation — MALL_MANAGER cannot access FM_REVIEW",
    goal=(
        "MALL_MANAGER sending FM action (approve_fm_review) on an SR in FM_REVIEW stage "
        "must be denied with a permission error"
    ),
    tags=["rbac", "role-isolation", "security"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            turns=[
                Turn(
                    message="I want to approve the FM review for this SR.",
                    note="MALL_MANAGER attempting FM_REVIEW action must be denied",
                    expect_keywords=[
                        "not authorised", "not authorized", "permission", "cannot",
                        "only", "fm", "role", "access denied",
                    ],
                ),
            ],
        ),
    ],
)


_S53 = LifecycleScenario(
    id=153,
    name="Role isolation — FM_MANAGER cannot access RDD_REVIEW",
    goal=(
        "FM_MANAGER attempting RDD submit on an SR in RDD_REVIEW must be denied"
    ),
    tags=["rbac", "role-isolation", "security"],
    phases=[
        _create_sr_phase(),
        _fm_approve_phase(),
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            turns=[
                Turn(
                    message="I want to submit the RDD handover report.",
                    note="FM_MANAGER attempting RDD_REVIEW action must be denied",
                    expect_keywords=[
                        "not authorised", "not authorized", "permission", "cannot",
                        "rdd", "only", "engineer", "role",
                    ],
                ),
            ],
        ),
    ],
)


_S54 = LifecycleScenario(
    id=154,
    name="Session isolation — two concurrent sessions produce independent state",
    goal=(
        "Two different MALL_MANAGER sessions run concurrently; "
        "each produces an independent SR with no cross-contamination of collected_data"
    ),
    tags=["session-isolation", "concurrent-sessions", "state-management"],
    phases=[
        # Session A: Under Armour
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Create a handover request for t0105712. Description: Session A test. "
                            "Start 2026-09-01, end 2026-09-03, FM Manager. No notes. No comments.",
                    note="Session A all fields",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    note="Session A SR submitted",
                    action="confirm",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference"],
                ),
            ],
        ),
        # Session B: Zara (runs independently in a different session)
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Create a handover request for t0419977. Description: Session B test. "
                            "Start 2026-09-05, end 2026-09-07, Operations. No notes. No comments.",
                    note="Session B — must show Zara fields, not Under Armour",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    note="Session B SR submitted with different sr_id",
                    action="confirm",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference"],
                ),
            ],
        ),
    ],
)


_S55 = LifecycleScenario(
    id=155,
    name="handover_type isolation — work permit SR then RDD SR in same session",
    goal=(
        "Creating a work permit SR then creating an RDD SR in the same session "
        "must not leak any work permit fields into the RDD payload"
    ),
    tags=["session-isolation", "handover-type", "state-management"],
    phases=[
        # First SR: standard handover
        _create_sr_phase(),
        # Second SR: attempt via RDD role (verifies no cross-contamination)
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="I need to review the RDD stage.",
                    note="DD Engineer opening review; must not see work permit fields",
                    expect_keywords=["rdd", "review", "handover", "guidelines", "date"],
                ),
            ],
        ),
    ],
)


# ─── rdd_action / fm_action persistence (S56–S58) ─────────────────────────────

_S56 = LifecycleScenario(
    id=156,
    name="rdd_action persisted within single turn — confirm_rdd_submit works standalone",
    goal=(
        "Sending confirm_rdd_submit without a prior submit_rdd_report action "
        "must work via single-turn confirm path — rdd_action set and confirmed in one turn"
    ),
    tags=["rdd-action", "single-turn-confirm", "state-management"],
    phases=[
        _create_sr_phase(),
        _fm_approve_phase(),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="DR_SR_HANDOVER_REPORT",
            turns=[
                Turn(
                    message=(
                        "Guidelines https://cenomi.example.com/rdd. "
                        "Document type: Handover Meeting Report. Document status approved. "
                        "Actual handover 20 August 2026. "
                        "Fitout 22 August to 5 September 2026. "
                        "No comments."
                    ),
                    note="All RDD fields collected",
                    expect_workflow_stage="RDD_REVIEW",
                ),
                Turn(
                    message="",
                    note="Direct confirm_rdd_submit — must set rdd_action=submit AND confirm in same turn",
                    action="confirm_rdd_submit",
                    expect_keywords=["submitted", "report", "rdd"],
                ),
            ],
        ),
    ],
)


_S57 = LifecycleScenario(
    id=157,
    name="fm_action single-turn confirm — confirm_fm_approve without prior approve_fm_review",
    goal=(
        "confirm_fm_approve without a prior approve_fm_review action must work "
        "via single-turn confirm path"
    ),
    tags=["fm-action", "single-turn-confirm", "state-management"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="SR_HANDOVER_CHECKLIST",
            turns=[
                Turn(
                    message="Unit readiness date is 10th August 2026.",
                    note="Date provided",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="confirm_fm_approve directly — must set fm_action=approve AND confirm",
                    action="confirm_fm_approve",
                    expect_keywords=["approved", "fm", "review"],
                ),
            ],
        ),
    ],
)


_S58 = LifecycleScenario(
    id=158,
    name="rdd_action reset after submission — final_approve starts fresh",
    goal=(
        "After successful RDD submit, rdd_action in backend_refs must be cleared "
        "so the final_approve turn starts fresh without the stale 'submit' action"
    ),
    tags=["rdd-action", "state-reset", "state-management"],
    phases=[
        _create_sr_phase(),
        _fm_approve_phase(),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="DR_SR_HANDOVER_REPORT",
            turns=[
                Turn(
                    message=(
                        "Guidelines https://cenomi.example.com/rdd. "
                        "Document type: Handover Meeting Report. Document status approved. "
                        "Actual handover 20 August 2026. "
                        "Fitout 22 August to 5 September 2026. "
                        "No comments."
                    ),
                    note="All RDD fields",
                ),
                Turn(
                    message="",
                    note="Submit report",
                    action="submit_rdd_report",
                ),
                Turn(
                    message="",
                    note="Confirm submission",
                    action="confirm_rdd_submit",
                    expect_keywords=["submitted", "report"],
                ),
                Turn(
                    message="",
                    note="Final approve — rdd_action must be 'final_approve' (not stale 'submit')",
                    action="approve_rdd_final",
                ),
                Turn(
                    message="",
                    note="Confirm final approve → SR_COMPLETED",
                    action="confirm_rdd_final_approve",
                    expect_workflow_stage="SR_COMPLETED",
                    expect_keywords=["completed", "approved", "final"],
                ),
            ],
        ),
    ],
)


# ─── Confirmation state lifecycle (S59–S62) ───────────────────────────────────

_S59 = LifecycleScenario(
    id=159,
    name="confirmation_status cleared after SR submit — FM turn not misread as confirmation",
    goal=(
        "After SR creation, confirmation_status must be None; "
        "the FM turn entry must not see CONFIRMED and skip to FM submission"
    ),
    tags=["confirmation-status", "state-lifecycle", "state-management"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="SR_HANDOVER_CHECKLIST",
            turns=[
                Turn(
                    message="Unit readiness date is 12th August 2026.",
                    note="FM turn — confirmation_status must be None; bot must ask for date normally",
                    expect_workflow_stage="FM_REVIEW",
                    expect_keywords=["unit", "readiness", "ready", "date", "2026"],
                ),
            ],
        ),
    ],
)


_S60 = LifecycleScenario(
    id=160,
    name="confirmation_status cleared after cancel — next turn not misread as confirmation",
    goal=(
        "After cancel SR, confirmation_status must be None; "
        "next CREATE_SR turn must start fresh field collection"
    ),
    tags=["confirmation-status", "cancel-cleanup", "state-management", "issue-4"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="",
                    note="Cancel SR after submission",
                    action="cancel_sr",
                    expect_keywords=["cancel", "sure"],
                ),
                Turn(
                    message="",
                    note="Confirm cancel",
                    action="confirm_cancel_sr",
                    expect_keywords=["cancelled"],
                    expect_no_active_agent=True,
                ),
                Turn(
                    message="I want to create a new handover request",
                    note="New intent; confirmation_status must be None — bot asks for lease (not submits)",
                    expect_keywords=["lease", "code", "brand", "mall"],
                ),
            ],
        ),
    ],
)


_S61 = LifecycleScenario(
    id=161,
    name="Rejected confirmation preserves collected_data — no fields lost",
    goal=(
        "User rejects the confirmation card (change mind); "
        "all previously collected fields must still be present in collected_data"
    ),
    tags=["confirmation-status", "field-preservation", "state-management"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message=(
                        "Create a handover request for t0105712. "
                        "Description: Rejection preservation test. "
                        "Start 2026-09-01, end 2026-09-03, FM Manager. "
                        "Notes: test notes. No comments."
                    ),
                    note="All fields collected; confirmation card shown",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="",
                    note="Cancel (reject) confirmation card",
                    action="cancel",
                    expect_keywords=["change", "what", "update", "edit", "modify"],
                ),
                Turn(
                    message="change end date to 2026-09-05",
                    note="Only end date changes; all other fields (desc, inspector, notes) must still be present",
                    expect_ui_type="confirmation_card",
                    expect_field_value={
                        "Description": "Rejection preservation test",
                        "Inspector": "FM_MANAGER",
                        "Notes": "test notes",
                        "End Date": "2026-09-05",
                    },
                ),
            ],
        ),
    ],
)


_S62 = LifecycleScenario(
    id=162,
    name="Re-confirmation after field correction shows updated value",
    goal=(
        "After rejecting confirmation and correcting a field, "
        "the re-shown confirmation card must display the UPDATED value"
    ),
    tags=["confirmation-status", "re-confirmation", "state-management"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message=(
                        f"Create a handover for t0208831. Description: Re-confirmation test. "
                        f"Start {_d(60)}, end {_d(62)}, Operations. No notes. No comments."
                    ),
                    note="All fields collected; confirmation card with end date (+62d)",
                    expect_ui_type="confirmation_card",
                    expect_field_value={"End Date": _d(62)},
                ),
                Turn(
                    message=f"change end date to {_d(65)}",
                    note="Field corrected; re-shown card must show +65d date",
                    expect_ui_type="confirmation_card",
                    expect_field_value={"End Date": _d(65)},
                ),
                Turn(
                    message="Confirm",
                    note="SR submitted with updated end date",
                    action="confirm",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference"],
                ),
            ],
        ),
    ],
)


# ─── Stale UI state (S63–S65) ─────────────────────────────────────────────────

_S63 = LifecycleScenario(
    id=163,
    name="Update button removed after FM submit — stage_complete emitted",
    goal=(
        "After FM approve, agent must emit stage_complete=true in response_ui; "
        "subsequent turns must not re-show the Update button"
    ),
    tags=["stage-complete", "fm-review", "ui-state", "issue-fm-update"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="SR_HANDOVER_CHECKLIST",
            turns=[
                Turn(
                    message="Unit readiness date is 10th August 2026.",
                    note="Date provided",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Approve FM review",
                    action="approve_fm_review",
                ),
                Turn(
                    message="",
                    note="Confirm FM approval — response must contain stage_complete=true",
                    action="confirm_fm_approve",
                    expect_keywords=["approved", "fm", "review"],
                ),
            ],
        ),
    ],
)


_S64 = LifecycleScenario(
    id=164,
    name="Confirm button shown after document upload (RDDPM)",
    goal=(
        "After uploading DR_SR_HANDOVER_REPORT in RDD_REVIEW, agent must emit "
        "confirmation_status=PENDING and a confirmation card so the UI shows the Confirm button"
    ),
    tags=["rdd-confirm-button", "document-upload", "issue-rdd-confirm"],
    phases=[
        _create_sr_phase(),
        _fm_approve_phase(),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="DR_SR_HANDOVER_REPORT",
            turns=[
                Turn(
                    message=(
                        "Guidelines https://cenomi.example.com/rdd. "
                        "Document type: Handover Meeting Report. Document status approved. "
                        "Actual handover 20 August 2026. "
                        "Fitout 22 August to 5 September 2026. "
                        "No comments."
                    ),
                    note="All RDD fields; document uploaded → Submit chip must appear",
                    expect_workflow_stage="RDD_REVIEW",
                ),
                Turn(
                    message="",
                    note="Submit report — response must have confirmation card",
                    action="submit_rdd_report",
                    expect_keywords=["confirm", "submit", "report"],
                ),
            ],
        ),
    ],
)


_S65 = LifecycleScenario(
    id=165,
    name="Final Approve buttons removed after SR_COMPLETED — workflow_complete emitted",
    goal=(
        "After final RDD approval, agent must emit workflow_complete=true; "
        "no further RDD action chips should appear"
    ),
    tags=["workflow-complete", "rdd-review", "ui-state"],
    phases=[
        _create_sr_phase(),
        _fm_approve_phase(),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="DR_SR_HANDOVER_REPORT",
            turns=[
                Turn(
                    message=(
                        "Guidelines https://cenomi.example.com/rdd. "
                        "Document type: Handover Meeting Report. Document status approved. "
                        "Actual handover 20 August 2026. "
                        "Fitout 22 August to 5 September 2026. "
                        "No comments."
                    ),
                    note="All RDD fields collected — workflow_complete test",
                    expect_workflow_stage="RDD_REVIEW",
                ),
                Turn(
                    message="",
                    note="Submit",
                    action="submit_rdd_report",
                ),
                Turn(
                    message="",
                    note="Confirm submit",
                    action="confirm_rdd_submit",
                    expect_keywords=["submitted", "report"],
                ),
                Turn(
                    message="",
                    note="Final approve",
                    action="approve_rdd_final",
                ),
                Turn(
                    message="",
                    note="Confirm final approve — must emit workflow_complete=true; no action chips",
                    action="confirm_rdd_final_approve",
                    expect_workflow_stage="SR_COMPLETED",
                    expect_keywords=["completed", "approved", "final"],
                ),
            ],
        ),
    ],
)


# ─── active_field binding — non-date fields (S66–S68) ─────────────────────────

_S66 = LifecycleScenario(
    id=166,
    name="active_field binds notes answer correctly",
    goal=(
        "When bot asks for notes (active_field=notes), user replies with free text; "
        "answer goes to notes — NOT another field like inspection_done_by"
    ),
    tags=["active-field-binding", "notes-routing", "issue-2"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Create a handover for t0105712. Description: Active field notes test. "
                            "Start 2026-09-01, end 2026-09-03, FM Manager.",
                    note="All fields except notes collected; bot asks for notes",
                    expect_keywords=["notes", "additional", "comment", "anything"],
                ),
                Turn(
                    message="Please coordinate with the building team before arriving",
                    note="Notes accepted; bot asks for comments",
                    expect_keywords=["comment", "additional"],
                ),
                Turn(
                    message="No comments",
                    note="Comments accepted; confirmation card shown with Notes row",
                    expect_ui_type="confirmation_card",
                    expect_field_value={"Notes": "Please coordinate with the building team before arriving"},
                ),
            ],
        ),
    ],
)


_S67 = LifecycleScenario(
    id=167,
    name="active_field binds inspection_done_by answer — notes unchanged",
    goal=(
        "When bot asks 'FM Manager or Operations?' (active_field=inspection_done_by), "
        "user replies 'FM Manager'; answer goes to inspection_done_by; notes unchanged"
    ),
    tags=["active-field-binding", "inspection-done-by", "issue-2"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message=(
                        f"Create a handover for t0208831. "
                        f"Description: Active field inspector test. Start {_d(60)}, end {_d(62)}."
                    ),
                    note="Lease, description, dates extracted; bot asks for inspector",
                    expect_keywords=["inspect", "fm manager", "operations", "who", "perform"],
                ),
                Turn(
                    message="FM Manager",
                    note="Answer must go to inspection_done_by=FM_MANAGER; notes must be None (not yet asked)",
                    expect_keywords=["notes", "comment", "anything", "additional"],
                ),
            ],
        ),
    ],
)


_S68 = LifecycleScenario(
    id=168,
    name="Explicit field update overrides active_field binding",
    goal=(
        "'Change description to X' on a notes-collection turn must update description, "
        "NOT the currently active notes field"
    ),
    tags=["active-field-binding", "explicit-update", "issue-2"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Create a handover for t0105712. Description: original description. "
                            "Start 2026-09-01, end 2026-09-03, FM Manager.",
                    note="All fields except notes collected; bot asks for notes",
                    expect_keywords=["notes", "additional", "comment"],
                ),
                Turn(
                    message="change description to updated description text",
                    note="Explicit update; must update description (not notes); bot should ask for notes again",
                    expect_keywords=["notes", "additional", "comment", "anything"],
                ),
            ],
        ),
    ],
)


# ─── Payload contract (S69–S72) ────────────────────────────────────────────────

_S69 = LifecycleScenario(
    id=169,
    name="unit_codes emitted in draft_preview — confirmation card includes unit_codes",
    goal=(
        "Agent response confirmation card must include a unit_codes field row "
        "populated from lease lookup (e.g. 'FF050')"
    ),
    tags=["payload-contract", "unit-codes", "ui-gap"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Create a handover request for t0105712. Description: Unit codes payload test. "
                            "Start 2026-09-01, end 2026-09-03, FM Manager. No notes. No comments.",
                    note="All fields collected; confirmation card must show Unit Code=FF050",
                    expect_ui_type="confirmation_card",
                    expect_field_value={"Unit Code": "FF050"},
                ),
            ],
        ),
    ],
)


_S70 = LifecycleScenario(
    id=170,
    name="Title auto-generated and emitted in confirmation card",
    goal=(
        "Agent confirmation card must show auto-generated title "
        "in format handover-{lease_code}-{slug}"
    ),
    tags=["payload-contract", "title-generation", "confirmation-card"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Create a handover request for t0105712. "
                            "Description: Standard fit-out inspection for new tenant. "
                            "Start 2026-09-01, end 2026-09-03, FM Manager. No notes. No comments.",
                    note="Title auto-generated as handover-t0105712-standard-fitout-inspection-for-new",
                    expect_ui_type="confirmation_card",
                    expect_field_value={
                        "Title": "handover-t0105712-standard-fitout-inspection-for-new",
                    },
                ),
            ],
        ),
    ],
)


_S71 = LifecycleScenario(
    id=171,
    name="Notes and description emitted separately in confirmation card",
    goal=(
        "Confirmation card must have distinct rows for Description and Notes; "
        "values must not be merged or duplicated between the two fields"
    ),
    tags=["payload-contract", "description-notes-separate", "issue-3"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message=(
                        f"Create a handover for t0419977. "
                        f"Description: Zara DFC pre-opening fit-out. "
                        f"Start {_d(60)}, end {_d(62)}, FM Manager."
                    ),
                    note="Fields collected; bot asks for notes",
                    expect_keywords=["notes", "additional", "comment"],
                ),
                Turn(
                    message="Coordinate with site team before starting",
                    note="Notes accepted; bot asks for comments",
                    expect_keywords=["comment", "additional"],
                ),
                Turn(
                    message="No comments",
                    note="Comments accepted; confirmation card must show BOTH Description and Notes as separate rows",
                    expect_ui_type="confirmation_card",
                    expect_field_value={
                        "Description": "Zara DFC pre-opening fit-out",
                        "Notes": "Coordinate with site team before starting",
                    },
                ),
            ],
        ),
    ],
)


_S72 = LifecycleScenario(
    id=172,
    name="Cancellation emits cancelled flag — response_ui.cancelled=true and workflow_stage=CANCELLED",
    goal=(
        "After successful SR cancellation via cancel_sr + confirm_cancel_sr, "
        "agent response must contain response_ui.cancelled=true "
        "and workflow_stage=CANCELLED"
    ),
    tags=["cancel", "cancelled-flag", "payload-contract", "issue-4"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="",
                    note="Cancel SR",
                    action="cancel_sr",
                    expect_keywords=["cancel", "sure"],
                ),
                Turn(
                    message="",
                    note="Confirm cancel — must emit cancelled=true and workflow_stage=CANCELLED",
                    action="confirm_cancel_sr",
                    expect_workflow_stage="CANCELLED",
                    expect_keywords=["cancelled", "cancel", "done"],
                    expect_no_active_agent=True,
                ),
            ],
        ),
    ],
)


# =============================================================================
# HANDED_OVER / Path B lifecycle scenarios (L150–L158)
# =============================================================================

# Past-date anchors for Path B "actual" date fields
_ACT_HANDOVER = (_TODAY - datetime.timedelta(days=90)).isoformat()
_ACT_FO_START = (_TODAY - datetime.timedelta(days=85)).isoformat()
_ACT_FO_END   = (_TODAY - datetime.timedelta(days=30)).isoformat()
_ACT_TRADING  = (_TODAY - datetime.timedelta(days=20)).isoformat()

# Slightly offset dates for multi-turn gradual scenario
_ACT_HANDOVER2 = (_TODAY - datetime.timedelta(days=80)).isoformat()
_ACT_FO_START2 = (_TODAY - datetime.timedelta(days=75)).isoformat()
_ACT_FO_END2   = (_TODAY - datetime.timedelta(days=25)).isoformat()
_ACT_TRADING2  = (_TODAY - datetime.timedelta(days=15)).isoformat()

# Violation: actual_trading_date before actual_fitout_end_date
_ACT_FO_END_VIO    = (_TODAY - datetime.timedelta(days=30)).isoformat()
_ACT_TRADING_VIO   = (_TODAY - datetime.timedelta(days=40)).isoformat()  # BEFORE fitout end → violation
_ACT_TRADING_FIXED = (_TODAY - datetime.timedelta(days=20)).isoformat()  # corrected


def _create_handed_over_phase(
    lease_code: str = "t0105712",
    all_in_one: bool = True,
) -> PhaseConfig:
    """Return a single-phase CREATE_HANDED_OVER_SR setup for MALL_MANAGER."""
    if all_in_one:
        return PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_HANDED_OVER_SR",
            turns=[
                Turn(
                    message=(
                        f"The unit for lease {lease_code} is already trading. "
                        f"Actual shop handover date: {_ACT_HANDOVER}. "
                        f"Actual fitout start: {_ACT_FO_START}. "
                        f"Actual fitout end: {_ACT_FO_END}. "
                        f"Actual trading date: {_ACT_TRADING}."
                    ),
                    note="All 4 actual dates + lease in one message; confirmation card expected",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    note="SR submitted; HANDED_OVER_COMPLETED stage returned",
                    action="confirm",
                    expect_workflow_stage="HANDED_OVER_COMPLETED",
                    expect_keywords=["approved", "automatically", "no further", "reference"],
                ),
            ],
        )
    # Multi-turn: return only the first turn (lease + handover date)
    return PhaseConfig(
        user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
        stage="CREATE_HANDED_OVER_SR",
        turns=[
            Turn(
                message=f"The unit for lease {lease_code} is already trading.",
                note="Lease + intent; bot should ask for actual date fields",
            ),
        ],
    )


_L150 = LifecycleScenario(
    id=150,
    name="already_trading_happy_path_single_turn",
    goal=(
        "All 4 dates + lease in one message → confirmation card → "
        "HANDED_OVER_COMPLETED (immediate approval, no FM/RDD steps)"
    ),
    tags=["handed-over", "path-b", "happy-path", "single-turn"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_HANDED_OVER_SR",
            turns=[
                Turn(
                    message=(
                        f"The unit for lease t0105712 is already trading. "
                        f"Actual shop handover date: {_ACT_HANDOVER}. "
                        f"Actual fitout start: {_ACT_FO_START}. "
                        f"Actual fitout end: {_ACT_FO_END}. "
                        f"Actual trading date: {_ACT_TRADING}."
                    ),
                    note="All 4 dates + lease in one message; confirmation card expected",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    note="SR submitted; immediately approved — HANDED_OVER_COMPLETED",
                    action="confirm",
                    expect_workflow_stage="HANDED_OVER_COMPLETED",
                    expect_keywords=["approved", "automatically", "reference", "no further"],
                ),
            ],
        ),
    ],
)


_L151 = LifecycleScenario(
    id=151,
    name="already_trading_multi_turn_gradual",
    goal=(
        "Lease first turn, dates across 2 turns, confirm → HANDED_OVER_COMPLETED; "
        "verifies field collection across multiple messages"
    ),
    tags=["handed-over", "path-b", "multi-turn", "gradual"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_HANDED_OVER_SR",
            turns=[
                Turn(
                    message="The unit for lease t0208831 is already trading.",
                    note="Lease identified as Nike Riyadh Park; bot asks for actual dates",
                ),
                Turn(
                    message=(
                        f"Shop handover was {_ACT_HANDOVER2}, fitout started {_ACT_FO_START2}."
                    ),
                    note="First 2 dates provided; bot asks for remaining dates",
                ),
                Turn(
                    message=(
                        f"Fitout ended {_ACT_FO_END2}, trading started {_ACT_TRADING2}."
                    ),
                    note="Last 2 dates provided; all fields collected; confirmation card",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    note="SR submitted; HANDED_OVER_COMPLETED returned",
                    action="confirm",
                    expect_workflow_stage="HANDED_OVER_COMPLETED",
                    expect_keywords=["approved", "automatically", "reference"],
                ),
            ],
        ),
    ],
)


_L152 = LifecycleScenario(
    id=152,
    name="already_trading_date_ordering_violation_then_correct",
    goal=(
        "actual_trading_date before actual_fitout_end_date → agent rejects → "
        "user corrects → HANDED_OVER_COMPLETED"
    ),
    tags=["handed-over", "path-b", "date-validation", "error-handling"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_HANDED_OVER_SR",
            turns=[
                Turn(
                    message=(
                        f"Lease t0105712 already trading. "
                        f"Handover: {_ACT_HANDOVER}. "
                        f"Fitout start: {_ACT_FO_START}. "
                        f"Fitout end: {_ACT_FO_END_VIO}. "
                        f"Trading: {_ACT_TRADING_VIO}."
                    ),
                    note=(
                        f"actual_trading_date ({_ACT_TRADING_VIO}) < actual_fitout_end_date "
                        f"({_ACT_FO_END_VIO}) — agent must block and ask to correct"
                    ),
                    expect_keywords=[
                        "date", "order", "before", "after", "trading", "fitout", "invalid", "correct"
                    ],
                    expect_no_submit=True,
                ),
                Turn(
                    message=f"Corrected trading date: {_ACT_TRADING_FIXED}.",
                    note="Valid trading date after fitout end; confirmation card shown",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    note="SR submitted; HANDED_OVER_COMPLETED",
                    action="confirm",
                    expect_workflow_stage="HANDED_OVER_COMPLETED",
                    expect_keywords=["approved", "automatically", "reference"],
                ),
            ],
        ),
    ],
)


_L153 = LifecycleScenario(
    id=153,
    name="already_trading_title_auto_default",
    goal=(
        "No title provided → confirm card must show 'Already Trading - {lease_code}' "
        "as the auto-generated title"
    ),
    tags=["handed-over", "path-b", "title-auto-generation"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_HANDED_OVER_SR",
            turns=[
                Turn(
                    message=(
                        f"Already trading for lease t0105712. "
                        f"Handover: {_ACT_HANDOVER}, fitout start: {_ACT_FO_START}, "
                        f"fitout end: {_ACT_FO_END}, trading: {_ACT_TRADING}."
                    ),
                    note="No title supplied; confirmation card must show auto-generated title",
                    expect_ui_type="confirmation_card",
                    expect_field_value={"Title": "Already Trading - t0105712"},
                ),
                Turn(
                    message="Confirm",
                    note="SR submitted with auto-generated title",
                    action="confirm",
                    expect_workflow_stage="HANDED_OVER_COMPLETED",
                    expect_keywords=["approved", "reference"],
                ),
            ],
        ),
    ],
)


_L154 = LifecycleScenario(
    id=154,
    name="already_trading_explicit_title",
    goal=(
        "Custom title provided → confirmed title preserved in payload; "
        "auto-generated title must NOT overwrite the user-supplied one"
    ),
    tags=["handed-over", "path-b", "title-explicit"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_HANDED_OVER_SR",
            turns=[
                Turn(
                    message=(
                        f"Already trading for lease t0208831. Title: Nike Riyadh Park Already Trading. "
                        f"Handover: {_ACT_HANDOVER}, fitout start: {_ACT_FO_START}, "
                        f"fitout end: {_ACT_FO_END}, trading: {_ACT_TRADING}."
                    ),
                    note="Explicit title supplied; confirmation card must show user title",
                    expect_ui_type="confirmation_card",
                    expect_field_value={"Title": "Nike Riyadh Park Already Trading"},
                ),
                Turn(
                    message="Confirm",
                    note="SR submitted with explicit title preserved",
                    action="confirm",
                    expect_workflow_stage="HANDED_OVER_COMPLETED",
                    expect_keywords=["approved", "reference"],
                ),
            ],
        ),
    ],
)


_L155 = LifecycleScenario(
    id=155,
    name="already_trading_with_optional_document",
    goal=(
        "MM uploads SR_HANDED_OVER_OTHER before confirm → SR still created; "
        "document ID appears in payload documents_ids"
    ),
    tags=["handed-over", "path-b", "optional-document", "sr-handed-over-other"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_HANDED_OVER_SR",
            upload_mock_document=True,
            document_type="SR_HANDED_OVER_OTHER",
            turns=[
                Turn(
                    message=(
                        f"Lease t0105712 already trading. "
                        f"Handover: {_ACT_HANDOVER}, fitout start: {_ACT_FO_START}, "
                        f"fitout end: {_ACT_FO_END}, trading: {_ACT_TRADING}."
                    ),
                    note="All dates provided; optional doc uploaded; confirmation card",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    note="SR submitted with optional document included",
                    action="confirm",
                    expect_workflow_stage="HANDED_OVER_COMPLETED",
                    expect_keywords=["approved", "reference"],
                ),
            ],
        ),
    ],
)


_L156 = LifecycleScenario(
    id=156,
    name="switch_from_initiate_to_already_trading",
    goal=(
        "User starts HANDOVER form (CREATE_SR) → switches to already_trading → "
        "Path A fields cleared → HANDED_OVER_COMPLETED"
    ),
    tags=["handed-over", "path-b", "mid-session-switch", "stale-clear"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="I want to create a handover service request for t0105712.",
                    note="Starts as Path A; bot asks for description + dates",
                ),
                Turn(
                    message="standard-fitout-desc-here",
                    note="Description provided; bot continues on Path A",
                ),
                Turn(
                    message="Actually, the unit is already trading. Start over.",
                    note=(
                        "User reveals Path B intent mid-session; stale-clear fires; "
                        "bot switches to CREATE_HANDED_OVER_SR flow"
                    ),
                    expect_workflow_stage="CREATE_HANDED_OVER_SR",
                    expect_keywords=["actual", "date", "handover", "trading", "already"],
                ),
                Turn(
                    message=(
                        f"Handover: {_ACT_HANDOVER}, fitout start: {_ACT_FO_START}, "
                        f"fitout end: {_ACT_FO_END}, trading: {_ACT_TRADING}."
                    ),
                    note="All 4 actual dates provided; confirmation card",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    note="SR submitted as HANDED_OVER — no Path A data in payload",
                    action="confirm",
                    expect_workflow_stage="HANDED_OVER_COMPLETED",
                    expect_keywords=["approved", "reference"],
                ),
            ],
        ),
    ],
)


_L157 = LifecycleScenario(
    id=157,
    name="switch_from_already_trading_back_to_initiate",
    goal=(
        "User starts already_trading → switches back to standard HANDOVER → "
        "Path B dates cleared → standard HANDOVER SR submitted"
    ),
    tags=["handed-over", "path-b", "mid-session-switch", "stale-clear"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_HANDED_OVER_SR",
            turns=[
                Turn(
                    message=(
                        f"Lease t0105712 already trading. "
                        f"Handover: {_ACT_HANDOVER}."
                    ),
                    note="Starts as Path B; partial actual date provided",
                ),
                Turn(
                    message="Actually, I want to create a standard inspection handover. Start over.",
                    note=(
                        "User switches back to Path A; stale-clear fires; "
                        "Path B dates must be wiped; bot re-enters CREATE_SR"
                    ),
                    expect_workflow_stage="CREATE_SR",
                    expect_keywords=["start", "date", "description", "inspection"],
                ),
                Turn(
                    message=(
                        f"Description: Standard fit-out. "
                        f"Start {_d(15)}, end {_d(19)}, FM Manager. No comments."
                    ),
                    note=(
                        "Path A fields provided; comments pre-filled as ''; "
                        "bot asks for notes (consistent with S49 design: notes and comments are distinct)"
                    ),
                ),
                Turn(
                    message="No notes.",
                    note="Notes declined; all optional fields satisfied; confirmation card shown",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    note="Standard HANDOVER SR submitted; no Path B dates in payload",
                    action="confirm",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference", "created"],
                ),
            ],
        ),
    ],
)


_L158 = LifecycleScenario(
    id=158,
    name="full_path_a_regression_after_path_b",
    goal=(
        "Full HANDOVER lifecycle (CREATE → FM → RDD → APPROVED) runs correctly "
        "after Path B changes live — Path A regression guard"
    ),
    tags=["path-a", "regression", "full-lifecycle", "after-path-b"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="SR_HANDOVER_CHECKLIST",
            turns=[
                Turn(
                    message=f"Unit readiness date is {_d(25)}.",
                    note="FM Manager provides date; expected_handover_date auto-computed",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Approve FM review",
                    action="approve_fm_review",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Confirm FM approval → fm_status=APPROVED",
                    action="confirm_fm_approve",
                    expect_keywords=["approved", "fm", "review"],
                ),
            ],
        ),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="RDD_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="DR_SR_HANDOVER_REPORT",
            turns=[
                Turn(
                    message=(
                        f"Guidelines: https://cenomi.example.com/regression-path-b. "
                        f"Document type: Handover Meeting Report. Document status approved. "
                        f"Actual handover {_d(30)}. "
                        f"Fitout {_d(32)} to {_d(50)}. No comments."
                    ),
                    note="All RDD fields in one message including doc type; trading_date auto-computed",
                    expect_workflow_stage="RDD_REVIEW",
                ),
                Turn(
                    message="",
                    note="Submit RDD report",
                    action="submit_rdd_report",
                ),
                Turn(
                    message="",
                    note="Confirm submission",
                    action="confirm_rdd_submit",
                    expect_keywords=["submitted"],
                ),
                Turn(
                    message="",
                    note="Final approve",
                    action="approve_rdd_final",
                ),
                Turn(
                    message="",
                    note="Confirm final approve → SR_COMPLETED",
                    action="confirm_rdd_final_approve",
                    expect_workflow_stage="SR_COMPLETED",
                    expect_keywords=["completed", "approved", "handover"],
                ),
            ],
        ),
    ],
)


# ─── Tier 0: Lifecycle smoke scenario ─────────────────────────────────────────

_S_SMOKE_FM = LifecycleScenario(
    id=203,
    name="Smoke — Quick FM_REVIEW approval",
    goal="Minimal 2-phase lifecycle (CREATE_SR → FM approve) to verify FM pipeline is alive",
    tags=["smoke", "fm-review"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_type="SR_HANDOVER_CHECKLIST",
            turns=[
                Turn(
                    message=f"Unit readiness date is {_d(25)}.",
                    note="FM Manager provides date; bot acknowledges",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Approve FM review",
                    action="approve_fm_review",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Confirm approval → fm_status=APPROVED",
                    action="confirm_fm_approve",
                    expect_keywords=["approved", "fm", "review"],
                ),
            ],
        ),
    ],
)


_L159 = LifecycleScenario(
    id=159,
    name="path_b_rbac_non_mall_manager_denied",
    goal=(
        "DD_ENGINEER is denied permission to create a HANDED_OVER SR; "
        "only MALL_MANAGER may submit Path B already-trading requests"
    ),
    tags=["path-b", "rbac", "dd-engineer", "permission-denied", "tier5"],
    phases=[
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="unit t0105712 is already trading",
                    note="DD_ENGINEER attempts HANDED_OVER path; must be denied",
                    expect_keywords=["not permitted", "not allowed", "mall manager"],
                ),
            ],
        ),
    ],
)


# ─── Master list and tier buckets ─────────────────────────────────────────────

#: Tier 0 — lifecycle smoke (FM approve only)
TIER0_LIFECYCLE_SCENARIOS: list[LifecycleScenario] = [_S_SMOKE_FM]

#: Tier 4 — FM_REVIEW scenarios (IDs 134–139)
TIER4_SCENARIOS: list[LifecycleScenario] = [_S34, _S35, _S36, _S37, _S38, _S39]

#: Tier 5 — RDD_REVIEW scenarios (IDs 140–145)
TIER5_SCENARIOS: list[LifecycleScenario] = [_S40, _S41, _S42, _S43, _S44, _S45]

#: Tier 6 — Full lifecycle + extended battle tests (IDs 146+)
TIER6_SCENARIOS: list[LifecycleScenario] = [
    _S46, _S47,                            # Full lifecycle (2)
    _S48, _S49,                            # MM optional upload gate (2)
    _S50, _S51, _S52, _S53, _S54, _S55,   # State contamination / session isolation (6)
    _S56, _S57, _S58,                      # rdd_action / fm_action persistence (3)
    _S59, _S60, _S61, _S62,               # Confirmation state lifecycle (4)
    _S63, _S64, _S65,                      # Stale UI state (3)
    _S66, _S67, _S68,                      # active_field binding (3)
    _S69, _S70, _S71, _S72,               # Payload contract (4)
]

#: Tier 7 — HANDED_OVER / Path B lifecycle scenarios (L150–L158)
TIER7_SCENARIOS: list[LifecycleScenario] = [
    _L150, _L151,                          # Happy paths (2)
    _L152,                                 # Date ordering violation (1)
    _L153, _L154,                          # Title: auto vs explicit (2)
    _L155,                                 # Optional document upload (1)
    _L156, _L157,                          # Mid-session path switch (2)
    _L158,                                 # Full Path A regression after Path B (1)
]

#: HANDED_OVER scenarios exported for selective runner use
HANDED_OVER_LIFECYCLE_SCENARIOS: list[LifecycleScenario] = TIER7_SCENARIOS


# =============================================================================
# TIER 9 — Fitout Confirmation SR lifecycle scenarios (L170–L182)
# =============================================================================

_L170 = LifecycleScenario(
    id=170,
    name="fos_create_yes_branch_single_turn",
    goal=(
        "DD_ENGINEER creates FOS SR in yes-branch in a single multi-field message; "
        "confirmation card appears and SR is submitted → SR_CREATED"
    ),
    tags=["fitout-confirm", "fos", "yes-branch", "single-turn", "tier6"],
    phases=[
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Confirm fitout has started for lease t0105712. Status: yes. Actual start date: " + _d(-10),
                    note="All fields in one message; confirmation card expected",
                    expect_ui_type="confirmation_card",
                    expect_field_value={"Actual Start Date": _d(-10)},
                ),
                Turn(
                    message="Confirm",
                    action="confirm",
                    note="SR submitted → SR_CREATED",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference", "created"],
                ),
            ],
        ),
    ],
)

_L171 = LifecycleScenario(
    id=171,
    name="fos_create_no_branch_with_delay",
    goal=(
        "DD_ENGINEER creates FOS SR in no-branch with delay reason; "
        "all planned dates and delay_reason collected → SR_CREATED"
    ),
    tags=["fitout-confirm", "fos", "no-branch", "delay-reason", "tier9"],
    phases=[
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Confirm fitout start for lease t0105712",
                    note="FOS flow initialised; agent asks for radio field",
                ),
                Turn(
                    message="no",
                    note="Fitout not started; agent asks for planned dates",
                    expect_keywords=["planned", "date"],
                ),
                Turn(
                    message=(
                        f"Planned start {_d(10)}, planned end {_d(40)}, "
                        f"planned trading {_d(60)}"
                    ),
                    note="Planned dates provided; agent asks for delay reason",
                    expect_keywords=["delay", "reason"],
                ),
                Turn(
                    message="Material procurement delays",
                    note="Delay reason provided; confirmation card expected",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    action="confirm",
                    note="SR submitted → SR_CREATED",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference"],
                ),
            ],
        ),
    ],
)

_L172 = LifecycleScenario(
    id=172,
    name="fos_approve_flow",
    goal=(
        "DD_ENGINEER opens an existing FOS SR (APPROVE_SR stage); "
        "fills in radio=yes + actual date → SR_APPROVED"
    ),
    tags=["fitout-confirm", "fos", "approve", "tier9"],
    phases=[
        _create_fos_sr_phase(),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="APPROVE_SR",
            open_existing_sr=True,
            turns=[
                Turn(
                    message="yes",
                    note="Approver confirms fitout has started; provides actual date",
                    expect_keywords=["actual", "date"],
                ),
                Turn(
                    message=_d(-10),
                    note="Actual date; confirmation card expected",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Update and Approve",
                    action="confirm_approve_sr",
                    note="Approved → SR_APPROVED",
                    expect_workflow_stage="SR_APPROVED",
                    expect_keywords=["approved"],
                ),
            ],
        ),
    ],
)

_L173 = LifecycleScenario(
    id=173,
    name="end_create_yes_branch",
    goal=(
        "DD_ENGINEER creates END SR in yes-branch; "
        "actual_fitout_complete_date collected → SR_CREATED"
    ),
    tags=["fitout-confirm", "end", "yes-branch", "tier6"],
    phases=[
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Confirm fitout is complete for lease t0105712. Yes, it's done. Date: " + _d(-5),
                    note="All fields in one message",
                    expect_ui_type="confirmation_card",
                    expect_field_value={"Actual Complete Date": _d(-5)},
                ),
                Turn(
                    message="Confirm",
                    action="confirm",
                    note="SR submitted",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference"],
                ),
            ],
        ),
    ],
)

_L174 = LifecycleScenario(
    id=174,
    name="end_create_no_branch",
    goal=(
        "DD_ENGINEER creates END SR in no-branch with planned dates and delay reason → SR_CREATED"
    ),
    tags=["fitout-confirm", "end", "no-branch", "tier9"],
    phases=[
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Confirm fitout end for t0105712 — not done yet",
                    note="END flow, no-branch",
                    expect_keywords=["planned", "date"],
                ),
                Turn(
                    message=f"Planned complete {_d(20)}, planned trading {_d(40)}",
                    note="Planned dates; agent asks for delay reason",
                    expect_keywords=["delay", "reason"],
                ),
                Turn(
                    message="Contractor delays",
                    note="Delay reason; confirmation card expected",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    action="confirm",
                    note="SR submitted",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted"],
                ),
            ],
        ),
    ],
)

_L175 = LifecycleScenario(
    id=175,
    name="end_approve_flow",
    goal="DD_ENGINEER approves an END SR → SR_APPROVED",
    tags=["fitout-confirm", "end", "approve", "tier9"],
    phases=[
        _create_end_sr_phase(),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="APPROVE_SR",
            open_existing_sr=True,
            turns=[
                Turn(
                    message="yes",
                    note="Fitout complete; actual date required",
                    expect_keywords=["actual", "date"],
                ),
                Turn(
                    message=_d(-5),
                    note="Actual complete date; confirmation card",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Update and Approve",
                    action="confirm_approve_sr",
                    note="Approved",
                    expect_workflow_stage="SR_APPROVED",
                    expect_keywords=["approved"],
                ),
            ],
        ),
    ],
)

_L176 = LifecycleScenario(
    id=176,
    name="trading_create_yes_branch",
    goal="MALL_MANAGER creates TRADING SR in yes-branch → SR_CREATED",
    tags=["fitout-confirm", "trading", "yes-branch", "tier6"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message=f"Unit t0105712 is trading. Confirm trading date {_d(-3)}",
                    note="All fields; confirmation card",
                    expect_ui_type="confirmation_card",
                    expect_field_value={"Actual Trading Date": _d(-3)},
                ),
                Turn(
                    message="Confirm",
                    action="confirm",
                    note="SR submitted",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference"],
                ),
            ],
        ),
    ],
)

_L177 = LifecycleScenario(
    id=177,
    name="trading_create_no_branch_no_delay_reason",
    goal=(
        "MALL_MANAGER creates TRADING SR in no-branch without delay_reason "
        "(optional for TRADING) → SR_CREATED"
    ),
    tags=["fitout-confirm", "trading", "no-branch", "delay-optional", "tier7"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Confirm trading for t0105712 — not started yet",
                    note="TRADING no-branch",
                    expect_keywords=["planned", "trading", "date"],
                ),
                Turn(
                    message=f"Planned trading date {_d(14)}",
                    note="Planned date only — delay_reason is optional for TRADING; confirmation card must appear without it",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    action="confirm",
                    note="SR submitted → SR_CREATED",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference", "created"],
                ),
            ],
        ),
    ],
)

_L178 = LifecycleScenario(
    id=178,
    name="trading_approve_flow",
    goal="MALL_MANAGER approves TRADING SR → SR_APPROVED",
    tags=["fitout-confirm", "trading", "approve", "tier9"],
    phases=[
        _create_trading_sr_phase(),
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="APPROVE_SR",
            open_existing_sr=True,
            turns=[
                Turn(
                    message="yes",
                    note="Trading confirmed; actual date required",
                    expect_keywords=["actual", "trading", "date"],
                ),
                Turn(
                    message=_d(-2),
                    note="Actual trading date; confirmation card",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Update and Approve",
                    action="confirm_approve_sr",
                    note="TRADING SR approved",
                    expect_workflow_stage="SR_APPROVED",
                    expect_keywords=["approved"],
                ),
            ],
        ),
    ],
)

_L179 = LifecycleScenario(
    id=179,
    name="fos_cancel_mid_flow",
    goal="User cancels FOS SR mid-flow → state cleared, workflow cancelled",
    tags=["fitout-confirm", "fos", "cancel", "mid-flow", "tier9"],
    phases=[
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Confirm fitout start for t0105712",
                    note="FOS flow started",
                ),
                Turn(
                    message="cancel",
                    action="cancel_sr",
                    note="Cancel mid-flow; state must be cleared",
                    expect_no_active_agent=True,
                ),
            ],
        ),
    ],
)

_L180 = LifecycleScenario(
    id=180,
    name="rbac_dd_engineer_cannot_approve_trading",
    goal=(
        "DD_ENGINEER is denied permission to approve a TRADING SR "
        "(only MALL_MANAGER/OPERATIONS may approve TRADING)"
    ),
    tags=["fitout-confirm", "rbac", "dd-engineer", "trading", "permission-denied", "tier9"],
    phases=[
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="I want to approve the trading confirmation SR",
                    note="DD_ENGINEER should be denied APPROVE_FITOUT_CONFIRM_SR for TRADING",
                    expect_keywords=["permission", "not", "allowed", "approve", "trading"],
                ),
            ],
        ),
    ],
)

_L181 = LifecycleScenario(
    id=181,
    name="rbac_mall_manager_cannot_approve_fos",
    goal="MALL_MANAGER is denied APPROVE_FITOUT_CONFIRM_SR for FOS (only DD_ENGINEER)",
    tags=["fitout-confirm", "rbac", "mall-manager", "fos", "permission-denied", "tier9"],
    phases=[
        PhaseConfig(
            user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="I want to approve the fitout start SR",
                    note="MALL_MANAGER should be denied for FOS",
                    expect_keywords=["permission", "not", "allowed", "approve"],
                ),
            ],
        ),
    ],
)

_L182 = LifecycleScenario(
    id=182,
    name="fitout_confirm_stale_state_cleared_after_terminal",
    goal=(
        "After FOS SR_CREATED (terminal), starting a new FOS request clears stale state "
        "so the new flow begins with a clean slate"
    ),
    tags=["fitout-confirm", "fos", "stale-state", "terminal", "tier9"],
    phases=[
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message=(
                        "Confirm fitout start for t0105712. Yes, started. "
                        "Actual start: " + _d(-10)
                    ),
                    note="FOS SR created",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    action="confirm",
                    note="SR_CREATED reached",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference"],
                ),
                Turn(
                    message="I want to create another fitout start confirmation for t0208831",
                    note=(
                        "New FOS request after terminal stage; stale state must be cleared. "
                        "Fresh CREATE_SR flow must begin without the old SR ID."
                    ),
                    expect_workflow_stage="CREATE_SR",
                ),
            ],
        ),
    ],
)


#: Tier 9 — Fitout Confirmation SR lifecycle scenarios (L170–L182)
#  (Kept as TIER9_SCENARIOS for backward compat; new names are TIER6/TIER7_LIFECYCLE_SCENARIOS)
TIER9_SCENARIOS: list[LifecycleScenario] = [
    _L170, _L171, _L172,     # FOS: yes-branch, no-branch, approve
    _L173, _L174, _L175,     # END: yes-branch, no-branch, approve
    _L176, _L177, _L178,     # TRADING: yes-branch, no-branch, approve
    _L179,                    # Cancel mid-flow
    _L180, _L181,             # RBAC checks
    _L182,                    # Stale state cleared
]


# =============================================================================
# TIER 0p — Pipeline Smoke (L298–L299, ~5 min)
# Validates SR ID handoff across phases.
# =============================================================================

_L299 = LifecycleScenario(
    id=299,
    name="handover_a_full_pipeline_smoke",
    goal=(
        "Handover A full 3-phase pipeline smoke: "
        "CREATE_SR → FM_REVIEW approve → SR_COMPLETED; "
        "validates SR ID forwarded correctly across all phases"
    ),
    tags=["smoke", "pipeline", "handover-a", "tier0p"],
    phases=[
        _create_sr_phase("t0105712"),
        _fm_approve_phase(),
    ],
)

_L298 = LifecycleScenario(
    id=298,
    name="fos_create_approve_pipeline_smoke",
    goal=(
        "FOS pipeline smoke: DD_ENGINEER creates FOS SR (yes-branch packed) → "
        "DD_ENGINEER approves → SR_APPROVED; validates SR ID handoff between phases"
    ),
    tags=["smoke", "pipeline", "fitout-fos", "tier0p"],
    phases=[
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="CREATE_SR",
            turns=[
                Turn(
                    message="Confirm fitout has started for lease t0105712. Status: yes. Actual start date: " + _d(-10) + ". No comments.",
                    note="FOS yes-branch packed; confirmation card expected",
                    expect_ui_type="confirmation_card",
                ),
                Turn(
                    message="Confirm",
                    action="confirm",
                    note="SR submitted → SR_CREATED",
                    expect_workflow_stage="SR_CREATED",
                    expect_keywords=["submitted", "reference", "created"],
                ),
            ],
        ),
        PhaseConfig(
            user_email=_DD[0], user_password=_DD[1], role=_DD[2],
            stage="APPROVE_SR",
            open_existing_sr=True,
            turns=[
                Turn(
                    message="",
                    action="confirm_approve_sr",
                    note="Approve FOS SR",
                    expect_workflow_stage="SR_APPROVED",
                    expect_keywords=["approved"],
                ),
            ],
        ),
    ],
)


# ─── Agent-centric tier map ────────────────────────────────────────────────────

#: Tier 0 — lifecycle smoke (FM approve only)
TIER0_LIFECYCLE_SCENARIOS: list[LifecycleScenario] = [_S_SMOKE_FM]

#: Tier 0p — Pipeline smoke (Handover A 3-phase + FOS create→approve)
TIER0P_SCENARIOS: list[LifecycleScenario] = [_L298, _L299]

#: Tier 3 lifecycle — Handover A full lifecycle (L134–L172, was TIER4+TIER5+TIER6)
TIER3_LIFECYCLE_SCENARIOS: list[LifecycleScenario] = [
    _S34, _S35, _S36, _S37, _S38, _S39,   # FM_REVIEW (L134–L139)
    _S40, _S41, _S42, _S43, _S44, _S45,   # RDD_REVIEW (L140–L145)
    _S46, _S47,                            # Full lifecycle (2)
    _S48, _S49,                            # MM optional upload gate (2)
    _S50, _S51, _S52, _S53, _S54, _S55,   # State contamination / session isolation (6)
    _S56, _S57, _S58,                      # rdd_action / fm_action persistence (3)
    _S59, _S60, _S61, _S62,               # Confirmation state lifecycle (4)
    _S63, _S64, _S65,                      # Stale UI state (3)
    _S66, _S67, _S68,                      # active_field binding (3)
    _S69, _S70, _S71, _S72,               # Payload contract (4)
]

# ─── Handover A stage-level tier exports (individual stage + RBAC testing) ────
#
# Breaking TIER3_LIFECYCLE_SCENARIOS into fine-grained sub-tiers:
#   3a  FM_REVIEW Happy       L134–L135
#   3b  FM_REVIEW Edge        L136–L137, L139
#   3c  FM_REVIEW Battle+RBAC L138
#   3d  RDD_REVIEW Happy      L140–L141
#   3e  RDD_REVIEW Edge       L142–L144
#   3f  RDD_REVIEW Battle+RBAC L145
#   3g  Full Lifecycle Happy   L146
#   3h  Full Lifecycle Edge    L147–L149
#   3i  Full Lifecycle Battle  L150–L172 (state isolation, persistence, contracts)

#: Tier 3a — FM_REVIEW Happy: date+approve, save-progress (L134–L135)
TIER3A_FM_HAPPY: list[LifecycleScenario] = [_S34, _S35]

#: Tier 3b — FM_REVIEW Edge: natural-language date, multi-field, Operations role (L136–L137, L139)
TIER3B_FM_EDGE: list[LifecycleScenario] = [_S36, _S37, _S39]

#: Tier 3c — FM_REVIEW Battle + RBAC: DD_ENGINEER denied (L138)
TIER3C_FM_BATTLE: list[LifecycleScenario] = [_S38]

#: Tier 3d — RDD_REVIEW Happy: all-fields-packed + turn-by-turn (L140–L141)
TIER3D_RDD_HAPPY: list[LifecycleScenario] = [_S40, _S41]

#: Tier 3e — RDD_REVIEW Edge: date-chain validation, APPROVED_WITH_CHANGES, NL dates (L142–L144)
TIER3E_RDD_EDGE: list[LifecycleScenario] = [_S42, _S43, _S44]

#: Tier 3f — RDD_REVIEW Battle + RBAC: FM_MANAGER denied (L145)
TIER3F_RDD_BATTLE: list[LifecycleScenario] = [_S45]

#: Tier 3g — Full Lifecycle Happy: CREATE→FM→RDD→SR_COMPLETED clean run (L146)
TIER3G_LIFECYCLE_HAPPY: list[LifecycleScenario] = [_S46]

#: Tier 3h — Full Lifecycle Edge: corrections at each stage + MM upload gate (L147–L149)
TIER3H_LIFECYCLE_EDGE: list[LifecycleScenario] = [_S47, _S48, _S49]

#: Tier 3i — Full Lifecycle Battle: state isolation, persistence, confirmation contracts (L150–L172)
TIER3I_LIFECYCLE_BATTLE: list[LifecycleScenario] = [
    _S50, _S51, _S52, _S53, _S54, _S55,   # State contamination / session isolation (6)
    _S56, _S57, _S58,                      # rdd_action / fm_action persistence (3)
    _S59, _S60, _S61, _S62,               # Confirmation state lifecycle (4)
    _S63, _S64, _S65,                      # Stale UI state (3)
    _S66, _S67, _S68,                      # active_field binding (3)
    _S69, _S70, _S71, _S72,               # Payload contract (4)
]

#: Convenience groups — run an entire stage at once
TIER_FM_REVIEW_ALL: list[LifecycleScenario] = TIER3A_FM_HAPPY + TIER3B_FM_EDGE + TIER3C_FM_BATTLE
TIER_RDD_REVIEW_ALL: list[LifecycleScenario] = TIER3D_RDD_HAPPY + TIER3E_RDD_EDGE + TIER3F_RDD_BATTLE
TIER_FULL_LIFECYCLE_ALL: list[LifecycleScenario] = (
    TIER3G_LIFECYCLE_HAPPY + TIER3H_LIFECYCLE_EDGE + TIER3I_LIFECYCLE_BATTLE
)

#: Tier 4 — Handover B Happy (L150–L151)
TIER4_SCENARIOS: list[LifecycleScenario] = [_L150, _L151]

#: Tier 5 — Handover B Edge + Battle (L152–L159)
TIER5_SCENARIOS: list[LifecycleScenario] = [
    _L152,                                 # Date ordering violation (1)
    _L153, _L154,                          # Title: auto vs explicit (2)
    _L155,                                 # Optional document upload (1)
    _L156, _L157,                          # Mid-session path switch (2)
    _L158,                                 # Full Path A regression after Path B (1)
    _L159,                                 # RBAC: non-MALL_MANAGER denied (1)
]

#: Tier 6 lifecycle — Fitout Happy (FOS/END/TRADING yes-branch: L170, L173, L176)
TIER6_LIFECYCLE_SCENARIOS: list[LifecycleScenario] = [_L170, _L173, _L176]

#: Tier 7 lifecycle — Fitout Edge + Battle (L171, L172, L174, L175, L177–L182)
TIER7_LIFECYCLE_SCENARIOS: list[LifecycleScenario] = [
    _L171, _L172,                          # FOS: no-branch, approve
    _L174, _L175,                          # END: no-branch, approve
    _L177, _L178,                          # TRADING: no-branch (complete), approve
    _L179,                                 # Cancel mid-flow
    _L180, _L181,                          # RBAC checks
    _L182,                                 # Stale state cleared
]

# ── Per-agent Fitout sub-tiers (lifecycle) ────────────────────────────────────
#
#   6a  FOS Happy        L170 — yes-branch single-turn create → SR_CREATED
#   6b  FOS Edge         L171 — no-branch with delay_reason → SR_CREATED
#   6c  FOS Battle+RBAC  L179, L181, L182 — cancel, RBAC, stale state
#   6d  FOS Lifecycle    L172 — APPROVE_SR stage: open existing FOS SR → SR_APPROVED
#   6e  END Happy        L173 — yes-branch create → SR_CREATED
#   6f  END Edge         L174 — no-branch with delay_reason → SR_CREATED
#   6g  END Lifecycle    L175 — APPROVE_SR stage: open existing END SR → SR_APPROVED
#   6h  TRADING Happy    L176 — yes-branch create → SR_CREATED
#   6i  TRADING Edge     L177 — no-branch, delay_reason optional → SR_CREATED
#   6j  TRADING Battle   L178, L180 — approve flow, DD_ENGINEER denied

#: Tier 6a lifecycle — FOS Happy: yes-branch single-turn create → SR_CREATED
TIER6A_FOS_HAPPY_LC: list[LifecycleScenario] = [_L170]

#: Tier 6b lifecycle — FOS Edge: no-branch with delay_reason collected → SR_CREATED
TIER6B_FOS_EDGE_LC: list[LifecycleScenario] = [_L171]

#: Tier 6c lifecycle — FOS Battle+RBAC: cancel mid-flow, MALL_MANAGER denied, stale state
TIER6C_FOS_BATTLE_LC: list[LifecycleScenario] = [_L179, _L181, _L182]

#: Tier 6d lifecycle — FOS Lifecycle (APPROVE_SR): open existing FOS SR → SR_APPROVED
TIER6D_FOS_LIFECYCLE: list[LifecycleScenario] = [_L172]

#: Tier 6e lifecycle — END Happy: yes-branch create → SR_CREATED
TIER6E_END_HAPPY_LC: list[LifecycleScenario] = [_L173]

#: Tier 6f lifecycle — END Edge: no-branch with planned dates + delay_reason → SR_CREATED
TIER6F_END_EDGE_LC: list[LifecycleScenario] = [_L174]

#: Tier 6g lifecycle — END Lifecycle (APPROVE_SR): open existing END SR → SR_APPROVED
TIER6G_END_LIFECYCLE: list[LifecycleScenario] = [_L175]

#: Tier 6h lifecycle — TRADING Happy: yes-branch create → SR_CREATED
TIER6H_TRADING_HAPPY_LC: list[LifecycleScenario] = [_L176]

#: Tier 6i lifecycle — TRADING Edge: no-branch, delay_reason optional → SR_CREATED
TIER6I_TRADING_EDGE_LC: list[LifecycleScenario] = [_L177]

#: Tier 6j lifecycle — TRADING Battle+RBAC: approve flow, DD_ENGINEER denied
TIER6J_TRADING_BATTLE_LC: list[LifecycleScenario] = [_L178, _L180]

#: Backward-compat aliases for old tier numbers
TIER6_SCENARIOS: list[LifecycleScenario] = TIER3_LIFECYCLE_SCENARIOS  # old naming
TIER7_SCENARIOS: list[LifecycleScenario] = TIER4_SCENARIOS + TIER5_SCENARIOS  # old naming

FITOUT_CONFIRM_LIFECYCLE_SCENARIOS: list[LifecycleScenario] = (
    TIER6_LIFECYCLE_SCENARIOS + TIER7_LIFECYCLE_SCENARIOS
)

#: HANDED_OVER scenarios exported for selective runner use
HANDED_OVER_LIFECYCLE_SCENARIOS: list[LifecycleScenario] = TIER4_SCENARIOS + TIER5_SCENARIOS

#: Backward-compatible master list (all lifecycle scenarios)
LIFECYCLE_SCENARIOS: list[LifecycleScenario] = (
    TIER3_LIFECYCLE_SCENARIOS
    + TIER4_SCENARIOS
    + TIER5_SCENARIOS
    + TIER6_LIFECYCLE_SCENARIOS
    + TIER7_LIFECYCLE_SCENARIOS
)
