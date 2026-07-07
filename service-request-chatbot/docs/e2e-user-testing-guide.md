# RDD Handover Lifecycle — E2E User Testing Guide

> **Purpose:** Step-by-step testing guide for the full RDD Handover SR lifecycle.
> Covers all 4 roles, Help Agent FAQ, RBAC cross-checks, and document upload testing.
>
> **Test fixtures:** `tests/fixtures/sample_fm_checklist.txt` (FM upload) and
> `tests/fixtures/sample_rdd_handover_report.txt` (RDD upload).

---

## Prerequisites

### 1. Start the Backend

```bash
cd service-request-chatbot/backend
uvicorn app.main:app --reload --port 8000
```

### 2. Start the Frontend

```bash
cd service-request-chatbot/frontend
npm run dev
```

Frontend available at: `http://localhost:3000`

### 3. Seed Test Users (first time only)

```bash
cd service-request-chatbot/backend
python scripts/seed_users.py
```

### 4. Enable RBAC Enforcement

In `backend/.env`:
```
RBAC_ENFORCE=true
```

Restart the backend after changing `.env`.

---

## Test Credentials

| Role | Email | Password | Phase |
|---|---|---|---|
| Mall Manager | aisha@cenomi.com | test1234 | Phase 1 — CREATE_SR |
| FM Manager | khalid@cenomi.com | test1234 | Phase 2 — FM_REVIEW |
| Operations | omar@cenomi.com | test1234 | Phase 2 — FM_REVIEW |
| RDD Project Manager | sara@cenomi.com | test1234 | Phase 3 — RDD_REVIEW |
| Admin | admin@cenomi.com | test1234 | All phases |

---

## Test Fixtures (Document Uploads)

| File | Upload as | Phase |
|---|---|---|
| `tests/fixtures/sample_fm_checklist.txt` | `SR_HANDOVER_CHECKLIST` | Phase 2 — FM_REVIEW |
| `tests/fixtures/sample_rdd_handover_report.txt` | `DR_SR_HANDOVER_REPORT` | Phase 3 — RDD_REVIEW |

---

## Phase 0 — Help Agent FAQ (Any Role)

**Login:** any account (e.g. `aisha@cenomi.com`)

| Step | Input | Expected |
|---|---|---|
| 1 | "What is a handover service request?" | Bot answers with FAQ content; `state.intent = "ASK_HELP"` |
| 2 | "Who is the RDD Project Manager?" | Bot explains the RDD PM role |
| 3 | "What documents does the RDD Project Manager need?" | Bot lists `DR_SR_HANDOVER_REPORT`, `SR_REJECTED_HANDOVER_REPORT`, Other |
| 4 | "How long does RDD review take?" | Bot explains no fixed SLA, RDD PM notified automatically after FM approval |
| 5 | "Hi" | Bot responds with greeting; `ui.type = "message"` |

**Verify:**
- `active_agent` is `null` on FAQ turns
- `faq_sources` array is present
- `state.workflow_stage` is `null`

---

## Phase 1 — Mall Manager Creates SR

**Login:** `aisha@cenomi.com` (Mall Manager)

| Step | Input | Expected |
|---|---|---|
| 1 | "I want to raise a handover service request" | Bot asks for lease code |
| 2 | Lease code, e.g. `"LC-001"` | Bot confirms lease details (brand, mall, unit codes) from lease lookup |
| 3 | `"Start June 1 2026, end June 10 2026"` | Bot stores dates; asks `inspection_done_by` |
| 4 | `"FM Manager will do the inspection"` | Bot stores `inspection_done_by = FM_MANAGER`; asks for notes |
| 5 | `"no notes"` | Bot stores empty notes; asks for comments |
| 6 | `"no comments"` | Confirmation card appears |
| 7 | Review card — verify all fields shown | Card shows: lease_code, brand, mall, unit_codes, city, contracted_area, title, description, startDate, endDate, inspection_done_by, notes, comments |
| 8 | Click **Confirm & Submit** | SR created; `sr_id` appears in LifecycleStepper |

**Verify:**
- `state.workflow_stage` advances (SR_CREATED or null after submit)
- `sr_id` present in response
- LifecycleStepper shows step 1 complete (green)
- Note the `sr_id` — you will need it for Phases 2 and 3

---

## Phase 2 — FM Manager Reviews SR

**Login:** `khalid@cenomi.com` (FM Manager) **or** `omar@cenomi.com` (Operations)

### 2a. Enter Existing SR

| Step | Action | Expected |
|---|---|---|
| 1 | Enter SR ID from Phase 1 in "Continue Existing SR" panel → click **Open** | `state.workflow_stage = "FM_REVIEW"` |
| 2 | SR Context panel appears (expand it) | Shows: mall, brand, unit_codes, lease_code, description |

### 2b. Upload FM Document (mandatory)

| Step | Action | Expected |
|---|---|---|
| 3 | In Document Upload panel → select **Handover Checklist** from dropdown | Dropdown shows: Handover Checklist, Site Survey, COP Checklist, Other |
| 4 | Click **Choose file** → select `tests/fixtures/sample_fm_checklist.txt` | Document appears in uploaded list with ✓ |

### 2c. Save Progress

| Step | Input / Action | Expected |
|---|---|---|
| 5 | Click **Save Progress** button (StageActions panel) | Bot prompts for `unit_readiness_date` (if not yet collected) |
| 6 | `"Unit ready July 15 2026"` | `unit_readiness_date = "2026-07-15"`; `expected_handover_date` AUTO-COMPUTED = `"2026-07-22"` (+7 days) |
| 7 | Bot asks for comments | Provide `"All units ready for inspection"` |
| 8 | Confirmation card appears | Card shows: lease_code, brand, mall, unit_readiness_date, expected_handover_date (read-only), comments; `confirmAction = "confirm_fm_save"` |
| 9 | Click **Confirm & Submit** | FM save PATCH sent; `state.fm_status = "IN_PROCESS"` |

**Verify:**
- `state.fm_status = "IN_PROCESS"` in response
- `confirmAction` in confirmation card = `"confirm_fm_save"` (visible in debug mode)

### 2d. Approve Review

| Step | Input / Action | Expected |
|---|---|---|
| 10 | Click **Approve Review** button | Confirmation card appears; `confirmAction = "confirm_fm_approve"` |
| 11 | Review card, click **Confirm & Submit** | FM approve PATCH sent; lifecycle advances |
| 12 | Check LifecycleStepper | Step 2 (FM Review) shows complete |

**Verify:**
- `state.workflow_stage` transitions toward `RDD_REVIEW` on the next sync
- Platform SR status moves to `APPROVED`

---

## Phase 3 — RDD Project Manager Submits Report

**Login:** `sara@cenomi.com` (RDD Project Manager)

### 3a. Enter Existing SR

| Step | Action | Expected |
|---|---|---|
| 1 | Enter SR ID from Phase 1 → click **Open** | `sr_status_sync` detects DD_ENGINEER IN_PROGRESS; `state.workflow_stage = "RDD_REVIEW"` |
| 2 | StageActions shows **Submit Report** button (not "Approve Review") | Correct role-gated button |
| 3 | SR Context panel shows FM context fields | unit_readiness_date, expected_handover_date visible |

### 3b. Upload Handover Report (mandatory)

| Step | Action | Expected |
|---|---|---|
| 4 | In Document Upload panel → select **Handover Meeting Report** | Dropdown shows: Handover Meeting Report, Rejected Fitout and Handover Report, Other |
| 5 | Click **Choose file** → select `tests/fixtures/sample_rdd_handover_report.txt` | Document appears in uploaded list with ✓ (`DR_SR_HANDOVER_REPORT`) |

### 3c. Provide RDD Fields

| Step | Input | Expected |
|---|---|---|
| 6 | `"Actual handover July 15, fitout starts July 16, fitout ends July 22, guidelines: https://cenomi.com/guidelines/handover"` | Bot stores 4 fields; `trading_date` AUTO-COMPUTED = `"2026-07-30"` (July 22 + 8 days) |
| 7 | SR Context panel (expand) | Shows `trading_date = "2026-07-30"` without user providing it |
| 8 | Bot asks for document status | Type `"Approve"` or `"Approve with changes"` |
| 9 | `document_status` extracted | `APPROVED` or `APPROVED_WITH_CHANGES` |
| 10 | Bot asks for comments | `"Handover completed successfully. All documentation verified."` |

### 3d. Submit Report

| Step | Input / Action | Expected |
|---|---|---|
| 11 | Click **Submit Report** | Confirmation card appears |
| 12 | Review card — verify all fields | Card shows: guideLineLink, **document_status** (dropdown), actual_handover_date, fitout_start_date, fitout_end_date, **trading_date** (read-only auto), comments; `confirmAction = "confirm_rdd_submit"` |
| 13 | For `document_status` inline edit — dropdown shows "Approve" / "Approve with Changes" | Select dropdown renders correctly |
| 14 | Click **Confirm & Submit** | POST `REPORT_SUBMITTED`; `state.rdd_status = "REPORT_SUBMITTED"` |
| 15 | StageActions now shows **Final Approve** only (not "Submit Report") | Correct — `rddStatus = "REPORT_SUBMITTED"` gates the button |

**Verify:**
- `state.rdd_status = "REPORT_SUBMITTED"` in HTTP response
- Submit Report button replaced by Final Approve button

### 3e. Final Approve

| Step | Input / Action | Expected |
|---|---|---|
| 16 | Click **Final Approve** | Confirmation card appears; `confirmAction = "confirm_rdd_final_approve"` |
| 17 | Click **Confirm & Submit** | PATCH `APPROVED`; `state.workflow_stage = "SR_COMPLETED"`, `state.rdd_status = "APPROVED"` |
| 18 | LifecycleStepper | All 4 steps green ✓ |

**Verify:**
- `state.workflow_stage = "SR_COMPLETED"`
- `state.rdd_status = "APPROVED"`
- LifecycleStepper shows complete

---

## RBAC Cross-Role Verification

These tests verify that each role is **blocked** from actions outside their phase.

| Test | Role | Action | Expected |
|---|---|---|---|
| R1 | Mall Manager (aisha) | Enter SR ID → try FM actions | Role denied: "FM Manager or Operations role required" |
| R2 | Mall Manager (aisha) | Ask "submit RDD report" | Intent not permitted; bot responds with FAQ/help |
| R3 | FM Manager (khalid) | Try to "create a new handover SR" | `CREATE_RDD_SERVICE_REQUEST` not in `APPROVE_RDD_SERVICE_REQUEST` intents; supervisor denies |
| R4 | RDD Project Manager (sara) | Try to "create a handover SR" | DD_ENGINEER has no create intent; supervisor routes to FAQ |
| R5 | RDD Project Manager (sara) | Enter SR still in FM_REVIEW stage | `sr_status_sync` routes to `fm_review_entry_node`; entry node denies DD_ENGINEER |
| R6 | FM Manager (khalid) | Enter SR in RDD_REVIEW stage | `sr_status_sync` routes to `rdd_review_entry_node`; entry node denies FM_MANAGER |

---

## Auto-Compute Verification

| Verification | How to test | Expected value |
|---|---|---|
| `expected_handover_date` | FM Manager provides `unit_readiness_date = "2026-07-15"` | `expected_handover_date = "2026-07-22"` (+7 days) |
| `trading_date` | RDD PM provides `fitout_end_date = "2026-07-22"` | `trading_date = "2026-07-30"` (+8 days) |
| Neither field is asked | Bot never asks "What is the trading date?" or "What is the expected handover date?" | Both fields appear read-only on confirmation card only |

---

## Document Status Verification

| Test | Input | Expected |
|---|---|---|
| D1 | RDD PM types "Approve" | `document_status = "APPROVED"` |
| D2 | RDD PM types "approve with changes" | `document_status = "APPROVED_WITH_CHANGES"` |
| D3 | On confirmation card — inline edit dropdown | Dropdown shows "Approve" and "Approve with Changes" (human-readable labels) |
| D4 | Select "Approve with Changes" in dropdown → Confirm & Submit | Payload contains `document_status_map[0].document_status = "APPROVED_WITH_CHANGES"` |

---

## Debug Mode

Enable **Debug mode** (toggle in chat header) to see:
- Session ID
- Current `workflow_stage`
- SR ID

To inspect the full API response, open browser DevTools → Network → `POST /api/chat/service-request` → Response body.

Key fields to check in `state`:
- `workflow_stage`
- `rdd_status` (present after RDD submit)
- `fm_status` (present after FM save/approve)
- `missing_fields` (should be empty before confirmation card)
- `ready_to_submit`
