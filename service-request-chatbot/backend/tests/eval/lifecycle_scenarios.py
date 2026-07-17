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

from dataclasses import dataclass, field
from typing import Optional

from tests.eval.scenarios import Turn


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

def _create_sr_phase(lease_code: str = "t0105712") -> PhaseConfig:
    """Return a standard CREATE_SR setup phase for Under Armour Jawharat Jeddah."""
    return PhaseConfig(
        user_email=_MALL[0], user_password=_MALL[1], role=_MALL[2],
        stage="CREATE_SR",
        turns=[
            Turn(
                message=(
                    f"Create a handover service request for lease {lease_code}. "
                    "Description: Standard fit-out inspection. "
                    "Start 2026-08-01, end 2026-08-05, done by FM Manager. No notes."
                ),
                note="All fields in one message; confirmation card expected",
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
                message="Unit readiness date is 10th August 2026.",
                note="Provide unit_readiness_date; expected_handover_date auto-computed",
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
    id=34,
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
    id=35,
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
    id=36,
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
    id=37,
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
    id=38,
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
    id=39,
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
    id=40,
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
                        "Actual handover date is 20th August 2026. "
                        "Fitout starts 22nd August 2026, ends 5th September 2026. "
                        "Document status is approved."
                    ),
                    note="All RDD fields extracted; trading_date auto-computed",
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
    id=41,
    name="RDD Review — Field Collection Turn by Turn",
    goal="DD Engineer provides each RDD field separately across multiple turns.",
    tags=["rdd-review", "field-collection"],
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
                    note="guideLineLink accepted",
                    expect_workflow_stage="RDD_REVIEW",
                ),
                Turn(
                    message="Actual handover date is 20 August 2026.",
                    note="actual_handover_date accepted",
                ),
                Turn(
                    message="Fitout starts on 22nd August.",
                    note="fitout_start_date accepted",
                ),
                Turn(
                    message="Fitout ends 5th September 2026.",
                    note="fitout_end_date accepted; trading_date auto-computed",
                ),
                Turn(
                    message="Document status is approve.",
                    note="document_status=APPROVED; bot asks only for comments/notes",
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
    id=42,
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
                        "Actual handover 20 August 2026. "
                        "Fitout starts September 5 and ends August 22 2026. "  # end < start
                        "Document status approved."
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
    id=43,
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
                        "Actual handover 20 August 2026. "
                        "Fitout 22 August to 5 September 2026. "
                        "Document status is approve with changes."
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
    id=44,
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
                        "Actual handover on the twentieth of August. "
                        "Fitout from the twenty-second of August until the fifth of September. "
                        "Document status approved."
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
    id=45,
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
    id=46,
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
                        "Actual handover 25 September 2026. "
                        "Fitout from 28 September to 15 October 2026. "
                        "Document status approved."
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
    id=47,
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
                        "Actual handover 20 September 2026. "
                        "Fitout 25 September to 15 October 2026. "
                        "Document status approved."
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
    id=48,
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
    id=49,
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


# ─── Master list ───────────────────────────────────────────────────────────────

LIFECYCLE_SCENARIOS: list[LifecycleScenario] = [
    _S34, _S35, _S36, _S37, _S38, _S39,   # FM_REVIEW (6)
    _S40, _S41, _S42, _S43, _S44, _S45,   # RDD_REVIEW (6)
    _S46, _S47,                            # Full lifecycle (2)
    _S48, _S49,                            # MM optional upload gate (2)
]
