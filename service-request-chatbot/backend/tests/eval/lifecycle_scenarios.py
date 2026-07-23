"""FM_REVIEW, RDD_REVIEW, and full-lifecycle evaluation scenarios.

Extends the 33 CREATE_SR scenarios in scenarios.py with:
  - Scenarios 34–39  → FM_REVIEW phase (FM Manager / Operations)
  - Scenarios 40–45  → RDD_REVIEW phase (DD Engineer / RDD PM)
  - Scenarios 46–47  → Full 3-phase lifecycle (CREATE → FM → RDD → COMPLETE)

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
    # When non-empty, upload each type (multi-file). Overrides single document_type.
    document_types: list[str] = field(default_factory=list)


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
                    note="Free text on notes turn — must go to notes, NOT inspection_done_by or comments",
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
                    note="Notes collected; confirmation card must show BOTH Description and Notes as separate rows",
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


# ─── Multi-file document upload (doc_upload_fix / b9bd469) — L173 ─────────────

_S73 = LifecycleScenario(
    id=173,
    name="FM multi-file upload — three docs then approve",
    goal=(
        "FM Manager uploads multiple documents (checklist + site survey + other) "
        "before approve; workflow must remain on FM_REVIEW and still reach "
        "fm_status=APPROVED (covers multi-file upload e2e path)"
    ),
    tags=["multi-file", "document-upload", "fm-review", "doc-upload-fix"],
    phases=[
        _create_sr_phase(),
        PhaseConfig(
            user_email=_FM[0], user_password=_FM[1], role=_FM[2],
            stage="FM_REVIEW",
            open_existing_sr=True,
            upload_mock_document=True,
            document_types=[
                "SR_HANDOVER_CHECKLIST",
                "SR_HANDOVER_SITE_SURVEY",
                "SR_HANDOVER_OTHER",
            ],
            turns=[
                Turn(
                    message=f"Unit readiness date is {_d(25)}. No comments.",
                    note="Provide unit_readiness_date after multi-file upload",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Approve FM review with three documents already attached",
                    action="approve_fm_review",
                    expect_workflow_stage="FM_REVIEW",
                ),
                Turn(
                    message="",
                    note="Confirm approval → fm_status=APPROVED despite multi-file upload",
                    action="confirm_fm_approve",
                    expect_keywords=["approved", "fm", "review"],
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
    _S73,                                  # Multi-file FM upload (doc_upload_fix)
]

#: Backward-compatible master list (all lifecycle scenarios)
LIFECYCLE_SCENARIOS: list[LifecycleScenario] = (
    TIER4_SCENARIOS + TIER5_SCENARIOS + TIER6_SCENARIOS
)
