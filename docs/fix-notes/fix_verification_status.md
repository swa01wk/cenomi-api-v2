# Fix Verification Status

**Verified:** 2026-07-18 (UI contract) · **Updated:** 2026-07-22 (lease eligibility + multi-file)  
**Scope:** `agent_ui_fixes.md` + `agent_ui_contract_and_frontend_alignment.md` + lease eligibility / multi-file upload  
**Codebase:** `cenomi-api-v2 / service-request-chatbot`

---

## TL;DR

- **Agent (backend): All fixes complete** — every agent-side item from both documents is implemented and verified in code.
- **Frontend: 7 items still pending** — these are frontend-only responsibilities. The agent already emits the correct signals for all of them; no further backend changes are needed.
- **2026-07-22 add-on:** Lease eligibility reasons + multi-file upload — code shipped on `doc_upload_fix`; e2e tracked via **S63 / S64 / L173** (see `lease_eligibility_and_multifile_upload.md`).

---

## Part 1 — `agent_ui_fixes.md` Agent Fixes

### MM (Mall Manager) — Agent Fixes

| # | Issue | Status | Code Evidence |
|---|---|---|---|
| 1 | **Time field not being asked** | ✅ Done | `inspectionTime` in `CREATE_SR_STAGE.required_fields` (`handover_schema.py`); question defined in `HANDOVER_FIELD_QUESTIONS` (`missing_field_service.py`); `merge_state_node` has `_USER_TIME_FIELDS` to preserve HH:MM and never strip it through `_to_date_only` |
| 2 | **Notes update intent confusion** | ✅ Done | `_remap_extracted_to_active_field()` in `merge_state_node` rebinds bare answers to the `active_field` the bot last asked; explicit guard comment prevents mirroring values between `notes` and `comments` |
| 3 | **Agent skips Description / mistakes Notes as Description** | ✅ Done | `description` and `notes` are both separate required fields with distinct questions; both in `OPTIONAL_FIELDS` so empty answers are accepted without blocking the flow |
| 4 | **Cancel button flow** | ✅ Done | `handover_entry_node` fully implements: `cancel_sr` action → sets `CANCEL_PENDING` → user confirms → `confirm_cancel_sr` PATCHes platform with `status=CANCELLED` → emits `{cancelled: true, workflow_stage: "CANCELLED"}`; text-based cancel phrases also detected via `_CANCEL_SR_PHRASES` |
| 5 | **Comments field never requested** | ✅ Done | `comments` added to `required_fields` in all three stages (CREATE_SR, FM_REVIEW, RDD_REVIEW) in `handover_schema.py`; dedicated question in `HANDOVER_FIELD_QUESTIONS`; in `OPTIONAL_FIELDS` so "no comments" / "skip" is accepted |
| 6 | **Document upload order (MM)** | ✅ Done | Optional MM upload is offered only after SR is submitted, via `MM_OPTIONAL_UPLOAD_STATUS_KEY` → `awaiting_choice` → `awaiting_upload` → `done` lifecycle managed in `review_decorations_service.py` and gated in `handover_entry_node` |

### RDDPM (RDD Project Manager) — Agent Fixes

| # | Issue | Status | Code Evidence |
|---|---|---|---|
| 7 | **Workflow order incorrect** | ✅ Done | `RDD_REVIEW_STAGE.required_fields` enforces order: `guideLineLink → rdd_upload_doc_type → actual_handover_date → fitout_start_date → fitout_end_date → comments`; `_rdd_required_dates_complete()` in `review_decorations_service.py` gates the upload card on `guideLineLink` + `rdd_upload_doc_type` only (not on dates) |

---

## Part 2 — `agent_ui_fixes.md` UI Validation Fixes

These are **frontend-only** items. The agent emits the correct data; the UI must bind/react to it.

| Issue | Agent Signal | Frontend Action Required |
|---|---|---|
| Unit Code not populated on form | `draft_preview.unit_codes` always present after lease lookup | Bind `draft_preview.unit_codes` → platform Unit Code field |
| Title not populated (only Description appears) | `draft_preview.title` auto-generated in `merge_state_node` as `handover-{lease_code}-{slug}` | Read `draft_preview.title` and populate the Title field |
| Notes entered in chat not reflected in form | `draft_preview.notes` stored and emitted on every turn | Sync `draft_preview.notes` → Notes form field on each response |
| Cancel button — form not closed after cancellation | `ui.cancelled = true` + `workflow_stage = "CANCELLED"` | Close / hide the SR form when `response.ui.cancelled === true` |
| FM Update button remains after approval | `response_ui.stage_complete = true` + `action = "fm_approved"` | Hide FM Update and Save Progress buttons when `stage_complete === true` |
| RDDPM Confirm button not shown after document upload | `ui.type = "confirmation_card"` + `confirmation_status = "PENDING"` | Show Confirm button whenever `ui.type === "confirmation_card"` |
| RDDPM Update + Final Approve buttons remain after Final Approve | `response_ui.workflow_complete = true` + `workflow_stage = "SR_COMPLETED"` | Hide all RDD action buttons when `workflow_complete === true` |

> **RDDPM Final Approve screen state** is handled on the agent side: after report submit, `rdd_status = "REPORT_SUBMITTED"` causes `build_review_decorations` to emit only the Final Approve chip (Submit chip is suppressed). No frontend change needed beyond rendering the chip.

---

## Part 3 — `agent_ui_contract_and_frontend_alignment.md` Known Gaps

| Gap | Title | Agent Status | Frontend Status |
|---|---|---|---|
| 1 | Cancel SR workflow | ✅ Implemented — `cancel_sr` → `CANCEL_PENDING` → `confirm_cancel_sr` → PATCH platform → `cancelled: true` | ⚠️ Add Cancel SR button (visible when `workflow_stage = "SR_CREATED"` and `sr_id` present); handle `CANCEL_PENDING` confirmation UI; close form on `cancelled = true` |
| 2 | Unit Code not populated on form | ✅ Agent always emits `unit_codes` in `draft_preview` | ⚠️ Bind `draft_preview.unit_codes` to the Unit Code field |
| 3 | Title not shown on form | ✅ Agent auto-generates `title` in `merge_state_node` | ⚠️ Read `draft_preview.title` and populate the Title field |
| 4 | Notes not reflected on form | ✅ Agent stores and emits `notes` in `draft_preview` | ⚠️ Sync `draft_preview.notes` to the Notes field on every response |
| 5 | FM Update button remains after approval | ✅ `fm_api_submission_node` emits `{stage_complete: true, action: "fm_approved"}` | ⚠️ Hide Update and Save buttons when `response_ui.stage_complete === true` |
| 6 | RDD Confirm button not shown after upload | ✅ Agent emits `ui.type = "confirmation_card"` with `confirmation_status = "PENDING"` | ⚠️ Show Confirm button when `ui.type === "confirmation_card"` |
| 7 | RDD buttons remain after SR_COMPLETED | ✅ `rdd_api_submission_node` emits `{workflow_complete: true, action: "rdd_final_approved", workflow_stage: "SR_COMPLETED"}` | ⚠️ Hide all RDD action buttons when `response_ui.workflow_complete === true` |
| 8 | `confirmation_status` not cleared after CREATE_SR | ✅ `api_submission_node` clears `confirmation_status: null, confirmation_required: false` alongside `workflow_stage: "SR_CREATED"` | N/A — no frontend change needed |
| 9 | `inspectionTime` field | ✅ Collected, stored as HH:MM, combined with `startDate`/`endDate` into UTC ISO in payload builder | Frontend should display `draft_preview.inspectionTime` as a row in the confirmation card |
| 10 | `comments` as distinct field from `notes` | ✅ `comments` in `required_fields` for all 3 stages; bot asks for notes then comments as separate turns | Frontend should display `draft_preview.notes` and `draft_preview.comments` as separate rows in the confirmation card |
| 11 | RDDPM field order | ✅ `RDD_REVIEW_STAGE.required_fields` enforces correct order; upload card gated on guideline + doc type only | N/A — field order is agent-driven via `missing_fields` |

---

## Pending Frontend Work — Action List

These 7 items are the only remaining work. All require frontend changes only.

1. **Unit Code** — bind `response.state.draft_preview.unit_codes` → platform SR Unit Code field
2. **Title** — bind `response.state.draft_preview.title` → platform SR Title field
3. **Notes** — sync `response.state.draft_preview.notes` → Notes field on every chat response
4. **Cancel SR button** — show when `workflow_stage === "SR_CREATED"` and `state.backend_refs.sr_id` is present; send `action_override = "cancel_sr"`
5. **Cancel SR form close** — when `response.ui.cancelled === true`, close the SR form and show a cancellation confirmation message
6. **FM stage_complete** — when `response.ui.stage_complete === true` (and `action === "fm_approved"`), hide the Update and Save Progress buttons
7. **RDD workflow_complete** — when `response.ui.workflow_complete === true`, hide all RDD action buttons (Submit Report, Final Approve) and show the completion screen

---

## Agent Signal Reference for Frontend

| Signal | Where emitted | Value |
|---|---|---|
| `response.ui.cancelled` | `handover_entry_node` on `confirm_cancel_sr` | `true` |
| `response.state.workflow_stage` on cancel | Same | `"CANCELLED"` |
| `response.state.confirmation_status` on cancel pending | `handover_entry_node` on `cancel_sr` | `"CANCEL_PENDING"` |
| `response.ui.stage_complete` on FM approve | `fm_api_submission_node` | `true` |
| `response.ui.stage_complete` on FM save or SR create | `fm_api_submission_node` / `api_submission_node` | `false` |
| `response.ui.workflow_complete` on RDD final approve | `rdd_api_submission_node` | `true` |
| `response.ui.stage_complete` on RDD submit | `rdd_api_submission_node` | `false` |
| `response.ui.type` for confirmation card | Any confirmation node | `"confirmation_card"` |
| `response.state.draft_preview.unit_codes` | `merge_state_node` via lease lookup | Unit code string |
| `response.state.draft_preview.title` | `merge_state_node._generate_title()` | `handover-{lease_code}-{slug}` |
| `response.state.draft_preview.notes` | `merge_state_node` | User-provided notes |
| `response.state.draft_preview.comments` | `merge_state_node` | User-provided comments |
| `response.state.draft_preview.inspectionTime` | `merge_state_node` | `HH:MM` (24-hour) |
