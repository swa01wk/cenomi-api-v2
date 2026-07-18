# Agent–UI Contract & Frontend Alignment

**Version:** 1.0  
**Audit date:** 2026-07-18  
**Codebase:** `cenomi-api-v2 / service-request-chatbot`

This document defines the complete contract between the LangGraph agent backend and the frontend UI. It covers:

1. All agent response schemas the UI depends on
2. Action identifiers the UI must send
3. Button-state indicators emitted by the agent
4. `workflow_stage` and `current_stage` values
5. Completion / cancellation / approval flags
6. Form refresh signals
7. Payload fields the UI must populate from agent responses
8. **Agent-owned** vs **Frontend-owned** vs **Shared** contract sections

---

## 1. HTTP Request Schema (Frontend → Agent)

Every chat turn POSTs to `/api/chat/{session_id}/message` with the following body:

```typescript
interface ChatRequest {
  message: string;                          // User's text input (may be "" for action-only turns)
  action_override?: string;                 // UI button action (see §4)
  corrected_fields?: Record<string, any>;   // Inline confirmation card edits
  selected_lease_id?: string;               // Lease selection from lease_selection card
  auth_token: string;                       // JWT bearer token
}
```

### Field rules

| Field | Required | Notes |
|---|---|---|
| `message` | Always | Can be `""` when `action_override` is set |
| `action_override` | When UI button clicked | Must be an exact string from §4 |
| `corrected_fields` | Optional | Sent alongside `action_override=confirm` for inline edits |
| `selected_lease_id` | Optional | Sent when user selects a lease from the `lease_selection` card |

---

## 2. HTTP Response Schema (Agent → Frontend)

```typescript
interface ChatResponse {
  // ── Core message ────────────────────────────────────────────────────────
  message: string;                         // Human-readable chatbot reply
  
  // ── Structured UI overlay ───────────────────────────────────────────────
  ui: AgentUI;
  
  // ── Response decorations (cards / chips / upload) ───────────────────────
  decorations?: ResponseDecorations;
  
  // ── Current agent state ─────────────────────────────────────────────────
  state: AgentState;
}

interface AgentUI {
  type: UIType;
  message?: string;                        // Same as ChatResponse.message for convenience
  
  // ── Confirmation card fields ─────────────────────────────────────────────
  fields?: ConfirmationField[];            // Present when type = "confirmation_card"
  confirmAction?: string;                  // Action to send on confirm button
  cancelAction?: string;                   // Action to send on cancel button
  
  // ── Lease selection card ─────────────────────────────────────────────────
  leases?: LeaseOption[];                  // Present when type = "lease_selection"
  
  // ── Stage / completion flags ─────────────────────────────────────────────
  stage_complete?: boolean;                // Emitted when FM or RDD stage finishes
  workflow_complete?: boolean;             // Emitted when SR_COMPLETED reached
  cancelled?: boolean;                     // Emitted when SR is cancelled
  
  // ── Field question ────────────────────────────────────────────────────────
  field?: string;                          // Field key being asked (type = "text_question")
}

type UIType =
  | "message"            // General chatbot message (no specific input expected)
  | "text_question"      // Bot is asking for a specific field value
  | "confirmation_card"  // All fields collected — awaiting user confirmation
  | "lease_selection"    // Multiple lease matches — user must pick one
  | "waiting"            // Processing / waiting state (rare)
  ;

interface ConfirmationField {
  key: string;           // Internal field name (e.g. "startDate")
  label: string;         // Human-readable label (e.g. "Start Date")
  value: string;         // Current value (e.g. "2026-06-01")
  editable: boolean;     // Whether the user can edit this field on the card
}

interface LeaseOption {
  id: string;            // Lease code (e.g. "t0208831")
  brand: string;
  mall: string;
  city: string;
  unit_codes: string;
  contracted_area: string;
}
```

---

## 3. Agent State Schema (in ChatResponse.state)

```typescript
interface AgentState {
  workflow_stage: WorkflowStage | null;
  workflow_stage_before?: WorkflowStage | null;
  active_agent: string | null;           // "handover_agent" | "fm_agent" | "rdd_agent" | null
  status: AgentStatus;
  confirmation_status: ConfirmationStatus | null;
  draft_preview?: DraftPreview;          // Full collected data snapshot
  ready_to_submit: boolean;              // True when all required fields collected
}

type WorkflowStage =
  | "CREATE_SR"
  | "FM_REVIEW"
  | "RDD_REVIEW"
  | "SR_CREATED"
  | "SR_COMPLETED"
  | "CANCELLED"         // Emitted after confirmed SR cancellation
  ;

type AgentStatus =
  | "WAITING_FOR_USER"  // Bot asked a question; awaiting user input
  | "IN_PROGRESS"       // Bot is processing; no user input needed
  | "SUBMITTED"         // API call succeeded
  | "COMPLETED"         // Workflow finished (final approval)
  | "FAILED"            // Error state
  ;

type ConfirmationStatus = "PENDING" | "CONFIRMED" | "REJECTED" | "CANCEL_PENDING" | null;
// CANCEL_PENDING: SR cancel confirmation is pending user response (yes/no)

interface DraftPreview {
  title?: string;
  description?: string;
  startDate?: string;           // YYYY-MM-DD
  endDate?: string;             // YYYY-MM-DD
  inspectionTime?: string;      // HH:MM (24-hour) — combined with startDate/endDate in payload
  inspection_done_by?: string;  // "FM_MANAGER" | "OPERATIONS"
  notes?: string;
  comments?: string;
  unit_codes?: string;          // ← UI MUST bind this to unitCode form field
  mall?: string;
  brand?: string;
  lease_code?: string;
  
  // FM_REVIEW fields
  unit_readiness_date?: string;
  expected_handover_date?: string;  // Auto-computed
  
  // RDD_REVIEW fields
  guideLineLink?: string;
  rdd_upload_doc_type?: string;
  document_status?: string;
  actual_handover_date?: string;
  fitout_start_date?: string;
  fitout_end_date?: string;
  trading_date?: string;            // Auto-computed
}
```

---

## 4. Action Identifiers (Frontend → Agent via `action_override`)

The frontend must send these exact string values in `action_override`.

### CREATE_SR actions

| Action string | When to send | Agent behaviour |
|---|---|---|
| `confirm` | User clicks Confirm on confirmation card | Sets `confirmation_status = "CONFIRMED"` → submits SR |
| `confirm_create_sr` | Alias for `confirm` | Same as `confirm` |
| `cancel_sr` | User clicks Cancel to cancel an **existing** SR | If SR exists: sets `confirmation_status = "CANCEL_PENDING"` + asks yes/no. If no SR yet: clears state immediately + `response_ui.cancelled = true` |
| `cancel` / `cancel_create_sr` | Aliases for `cancel_sr` | Same as `cancel_sr` |
| `confirm_cancel_sr` | User confirms SR cancellation (after CANCEL_PENDING) | PATCHes platform with `status=CANCELLED`, clears all state, emits `response_ui = {cancelled: true, workflow_stage: "CANCELLED"}` |
| `decline_cancel_sr` | User declines SR cancellation | Restores previous stage; clears `CANCEL_PENDING` |
| `cancel_update` | User cancels an inline card edit | Returns "Update cancelled" message |
| `upload_document` | User clicks upload button | Returns upload interface message |

### Optional MM upload actions (CREATE_SR post-submission)

| Action string | When to send | Agent behaviour |
|---|---|---|
| `accept_optional_upload` | User clicks Yes on upload offer | Shows upload card |
| `skip_optional_upload` | User clicks No on upload offer | Skips upload; `mm_optional_upload_status = "done"` |
| `continue_optional_upload` | User clicks Continue on upload card | Closes upload card; resumes flow |

### FM_REVIEW actions

| Action string | When to send | Agent behaviour |
|---|---|---|
| `save_fm_progress` | User clicks Save Progress | PATCH SR → `IN_PROCESS` |
| `approve_fm_review` | User clicks Approve Review | Shows FM confirmation card |
| `confirm_fm_approve` | User clicks Confirm on FM card | PATCH SR → `APPROVED`; `stage_complete = true` |
| `confirm_fm_save` | User clicks Confirm on FM save card | PATCH SR → `IN_PROCESS` |

### RDD_REVIEW actions

| Action string | When to send | Agent behaviour |
|---|---|---|
| `select_rdd_doc_meeting` | Chip: Handover Meeting Report | Sets `rdd_upload_doc_type = "DR_SR_HANDOVER_REPORT"` |
| `select_rdd_doc_rejected` | Chip: Rejected Report | Sets `rdd_upload_doc_type = "SR_REJECTED_HANDOVER_REPORT"`; `document_status = "DISAPPROVED"` |
| `select_rdd_doc_other` | Chip: Other | Sets `rdd_upload_doc_type = "SR_HANDOVER_OTHER"` |
| `select_rdd_status_approved` | Chip: Approve | Sets `document_status = "APPROVED"` |
| `select_rdd_status_approved_with_changes` | Chip: Approve with Changes | Sets `document_status = "APPROVED_WITH_CHANGES"` |
| `submit_rdd_report` | Submit Report chip | Opens RDD submit confirmation |
| `confirm_rdd_submit` | Confirm on RDD submit card | POST report → `REPORT_SUBMITTED` |
| `approve_rdd_final` | Final Approve chip | Opens final approval confirmation |
| `confirm_rdd_final_approve` | Confirm on final approval card | PATCH → `APPROVED`; `workflow_complete = true` |

---

## 5. Button-State Indicators (Agent → Frontend)

The frontend must show/hide buttons based on these agent signals:

### Confirmation card buttons

| Signal | Condition | Button state |
|---|---|---|
| `ui.type = "confirmation_card"` | Agent emits confirmation card | Show **Confirm** + **Cancel** buttons |
| `ui.confirmAction` | Present on confirmation card | Use as `action_override` for Confirm button |
| `ui.cancelAction` | Present on confirmation card | Use as `action_override` for Cancel button |

### FM_REVIEW button states

| Agent signal | UI action |
|---|---|
| `decorations.suggestions.options[].action = "save_fm_progress"` | Show **Save Progress** button |
| `decorations.suggestions.options[].action = "approve_fm_review"` | Show **Approve Review** button |
| `ui.stage_complete = true` | **Hide** Update and Save buttons (FM stage done) |

### RDD_REVIEW button states

| Agent signal | UI action |
|---|---|
| `decorations.suggestions.options[].action = "submit_rdd_report"` | Show **Submit Report** button |
| `decorations.suggestions.options[].action = "approve_rdd_final"` | Show **Final Approve** button |
| `state.backend_refs.rdd_status = "REPORT_SUBMITTED"` | Show Final Approve only (not Submit) |
| `ui.workflow_complete = true` | **Hide** all RDD action buttons |

### Cancel SR button state

| Agent signal | UI action |
|---|---|
| `state.workflow_stage = "SR_CREATED"` AND `state.backend_refs.sr_id` present | Show **Cancel SR** button (send `action_override = "cancel_sr"`) |
| `ui.cancelled = true` | Close the form / show "SR cancelled" message |

> ⚠️ **`cancelled` and `cancel_sr` action are NOT YET IMPLEMENTED.** This is Phase 1 work.

---

## 6. Stage Values and Transitions

### `workflow_stage` lifecycle

```
null → CREATE_SR → SR_CREATED → FM_REVIEW → RDD_REVIEW → SR_COMPLETED
                              ↘ (if no RDD op) → SR_COMPLETED
```

| `workflow_stage` value | Meaning | Frontend action |
|---|---|---|
| `null` | No active workflow | Show greeting / intent classifier |
| `CREATE_SR` | MM collecting fields | Show field collection UI |
| `SR_CREATED` | SR submitted; awaiting FM | Show SR reference number; optionally poll |
| `FM_REVIEW` | FM reviewing | Show FM review UI with action buttons |
| `RDD_REVIEW` | RDD PM reviewing | Show RDD review UI with upload + action buttons |
| `SR_COMPLETED` | Final approval done | Show completion confirmation; hide all action buttons |
| `CANCELLED` | SR cancelled (⚠️ not yet emitted) | Show cancellation message; close form |

### `status` values

| Status | Meaning |
|---|---|
| `WAITING_FOR_USER` | Bot asked a question; show input field |
| `IN_PROGRESS` | Bot processing; show loading state |
| `SUBMITTED` | API call succeeded (SR created or stage submitted) |
| `COMPLETED` | Workflow fully completed |
| `FAILED` | Error — show error message from `response.message` |

---

## 7. Completion / Cancellation / Approval Flags

These boolean flags are emitted in `response.ui` (some require Phase 1–3 implementation):

| Flag | Emitted when | Current status | UI action |
|---|---|---|---|
| `ui.stage_complete = true` | FM review approved (`fm_action=approve`) | ✅ **Now emitted** — `response_ui.action = "fm_approved"` | Hide FM Update/Save buttons |
| `ui.stage_complete = false` | FM progress saved (`fm_action=save`) | ✅ Now emitted — `response_ui.action = "fm_saved"` | Keep FM Update/Save buttons visible |
| `ui.stage_complete = false` | CREATE_SR submitted | ✅ Now emitted — `response_ui.workflow_stage = "SR_CREATED"` | Show FM review entry point |
| `ui.workflow_complete = true` | SR_COMPLETED (RDD final approve) | ✅ **Now emitted** — `response_ui.action = "rdd_final_approved"` | Hide all RDD action buttons; show completion screen |
| `ui.stage_complete = false` | RDD report submitted (`rdd_action=submit`) | ✅ Now emitted — `response_ui.action = "rdd_submitted"` | Keep Final Approve button visible |
| `ui.cancelled = true` | SR cancelled via `cancel_sr` + confirmed | ✅ **Now implemented** — `response_ui.workflow_stage = "CANCELLED"` | Close form; show "SR cancelled" confirmation |
| `confirmation_status = "PENDING"` | Confirmation card shown | ✅ Emitted correctly | Show Confirm + Cancel buttons |
| `confirmation_status = "CANCEL_PENDING"` | Cancel SR confirmation awaiting | ✅ **New** — emitted after `cancel_sr` action when SR exists | Show "Confirm cancel" + "Keep request" buttons |
| `confirmation_status = "CONFIRMED"` | User confirmed | ✅ Set correctly | Trigger submission |
| `confirmation_status = null` | After any submission (CREATE, FM, RDD) | ✅ Now cleared consistently across all submission nodes | Do not re-show stale card |

### `response_ui` shape (full schema)

```typescript
interface ResponseUI {
  // Completion / cancellation flags
  stage_complete?: boolean;    // True when FM approved; false when FM saved or SR created
  workflow_complete?: boolean; // True when RDD final approval completes (SR_COMPLETED)
  cancelled?: boolean;         // True when SR cancelled (confirm_cancel_sr)

  // Action identifier — what just happened
  action?: "fm_saved" | "fm_approved" | "rdd_submitted" | "rdd_final_approved";

  // Current workflow position
  workflow_stage?: string;     // Always present in submission responses
}
```

---

## 8. Form Refresh Signals

When the agent updates field values, the frontend must sync the form:

| Agent data | Form field to update | Current status |
|---|---|---|
| `draft_preview.title` | Title field | ⚠️ **Frontend not reading this** — UI gap |
| `draft_preview.notes` | Notes field | ⚠️ **Frontend not syncing** — UI gap |
| `draft_preview.unit_codes` | Unit Code field | ⚠️ **Frontend not binding** — UI gap |
| `draft_preview.description` | Description field | Should be bound |
| `draft_preview.startDate` | Start Date field | Should be bound |
| `draft_preview.endDate` | End Date field | Should be bound |
| `draft_preview.inspection_done_by` | Inspector field | Should be bound |

### Confirmation card field refresh

When `corrected_fields` is sent alongside a confirmation turn, the agent writes the updated values to `collected_data` and re-emits a fresh `confirmation_card`. The frontend must:
1. Clear the old confirmation card
2. Re-render the new card with updated `ui.fields`
3. Not re-send the prior `corrected_fields` on the next turn

---

## 9. Payload Fields for Platform Form Population

The following agent-collected fields map to platform SR form fields. The UI must read from `draft_preview` (or `state.collected_data`) and populate the corresponding form fields.

### CREATE_SR → Platform SR Form

| Agent field | Platform form field | Notes |
|---|---|---|
| `title` | Title | Auto-generated as `handover-{lease_code}-{slug}` |
| `description` | Description | User-provided |
| `startDate` | Start Date | `YYYY-MM-DD` |
| `endDate` | End Date | `YYYY-MM-DD` |
| `inspection_done_by` | Inspection Done By | `FM_MANAGER` \| `OPERATIONS` |
| `notes` | Notes | Free-form |
| `comments` | Comments | ⚠️ Currently never collected by agent |
| `unit_codes` | Unit Code | Backend-derived from lease lookup |
| `lease_code` | Lease Code | From user or lease selection |
| `mall` | Mall | Backend-derived |
| `brand` | Brand | Backend-derived |
| `city` | City | Backend-derived |
| `contracted_area` | Contracted Area | Backend-derived |

### FM_REVIEW → Platform SR Form

| Agent field | Platform form field | Notes |
|---|---|---|
| `unit_readiness_date` | Unit Readiness Date | User-provided |
| `expected_handover_date` | Expected Handover Date | Auto-computed: `unit_readiness_date + 7 days` |
| `comments` | FM Comments | ⚠️ Currently not collected |

### RDD_REVIEW → Platform SR Form

| Agent field | Platform form field | Notes |
|---|---|---|
| `guideLineLink` | Guidelines Link | User-provided |
| `rdd_upload_doc_type` | Document Type | Chip selection |
| `document_status` | Document Status | `APPROVED` \| `APPROVED_WITH_CHANGES` \| `DISAPPROVED` |
| `actual_handover_date` | Actual Handover Date | User-provided |
| `fitout_start_date` | Fitout Start Date | User-provided |
| `fitout_end_date` | Fitout End Date | User-provided |
| `trading_date` | Trading Date | Auto-computed: `fitout_end_date + 8 days` |

---

## 10. Document Upload Contract

### FM document upload

| Property | Value |
|---|---|
| Accepted types | `SR_HANDOVER_CHECKLIST`, `SR_HANDOVER_SITE_SURVEY`, `SR_HANDOVER_OTHER` |
| Required count | At least 1 of the 3 types |
| Upload endpoint | `POST /api/documents` with `session_id` + `sr_id` in context |
| Upload context | `decorations.document_requirement.uploadContext.session_id` + `.sr_id` |
| Gate condition | FM save/approve is blocked until ≥1 document uploaded |

### RDD document upload

| Property | Value |
|---|---|
| Accepted types | `DR_SR_HANDOVER_REPORT`, `SR_REJECTED_HANDOVER_REPORT`, `SR_HANDOVER_OTHER` |
| Mandatory type | `DR_SR_HANDOVER_REPORT` (Meeting Report) |
| Upload card shown | Only when `rdd_upload_doc_type` is set AND dates are present (⚠️ fix needed — dates should not gate this) |
| Gate condition | Submit Report chip hidden until Meeting Report uploaded |
| Upload context | Same pattern as FM |

### MM optional document upload

| Property | Value |
|---|---|
| Accepted types | `SR_HANDOVER_OTHER` only |
| Required | No — optional |
| Offer timing | After all fields collected AND SR submitted |
| Actions | `accept_optional_upload` → show card; `skip_optional_upload` → skip; `continue_optional_upload` → done |

---

## 11. Agent-Owned vs Frontend-Owned vs Shared Contract

### Agent-owned (backend controls)

These values are computed, stored, and emitted exclusively by the agent. The frontend must not override them.

| Data | Source | Frontend role |
|---|---|---|
| `workflow_stage` | Graph state transitions | Read-only; use to determine which UI to show |
| `confirmation_status` | `handover_entry_node` | Read-only; do not send this field |
| `draft_preview` / `collected_data` | Merge state node | Display only; update via `corrected_fields` |
| `title` | Auto-generated by `merge_state_node._generate_title()` | Display only — do not allow user to edit |
| `expected_handover_date` | Auto-computed: `unit_readiness_date + 7` | Display only — read-only in form |
| `trading_date` | Auto-computed: `fitout_end_date + 8` | Display only — read-only in form |
| `unit_codes`, `city`, `contracted_area` | Backend API lookup | Display only — not user-editable |
| `tenant_profile_id`, `property_id`, `brand_id`, `lease_id` | Backend API lookup | Never display; never send |
| Action chips (`decorations.suggestions`) | Computed by `review_decorations_service` | Render as buttons; send value as `action_override` |

### Frontend-owned (UI controls)

These are frontend responsibilities that the agent does not manage.

| Responsibility | Notes |
|---|---|
| Form layout and field ordering on the platform form | Agent provides data; form layout is frontend's |
| File upload UX (progress bar, retry, file size) | Agent only stores document metadata |
| Authentication / JWT token management | Agent validates token but frontend manages refresh |
| Session creation | Frontend creates session_id via `POST /api/sessions` |
| Polling SR status (if async) | Agent does not push updates; frontend must poll |
| Error display formatting | Agent provides `response.message`; formatting is frontend's |
| Toast notifications | Frontend decision |

### Shared contract (both must agree)

These require coordination between agent and frontend:

| Signal | Agent responsibility | Frontend responsibility |
|---|---|---|
| `action_override` strings | Define and document exact strings | Send exact strings; no typos or aliases |
| `corrected_fields` keys | Accept and validate in `merge_state_node` | Send using exact `collected_data` key names |
| `selected_lease_id` | Accept in `lease_lookup_node` | Send when user picks from `lease_selection` card |
| `ui.type` values | Emit correct type per situation | Render correct card based on `ui.type` |
| `decorations.suggestions.options[].action` | Emit correct action strings | Use `.action` value (not `.value`) as `action_override` |
| `ui.stage_complete` | Emit after FM approval ⚠️ (Phase 2) | Hide Update button when `true` |
| `ui.workflow_complete` | Emit after RDD final approve ⚠️ (Phase 3) | Hide action buttons when `true` |
| `ui.cancelled` | Emit after SR cancel ⚠️ (Phase 1) | Close form when `true` |

---

## 12. Known UI Contract Gaps — Agent Fixes Applied / Frontend Remaining

### ✅ Gap 1 — Cancel SR — FIXED (agent)

**Was:** `action_override = "cancel"` triggered "what would you like to change?" — not a cancellation.  
**Fixed:** New action `cancel_sr` → sets `CANCEL_PENDING` → user confirms → `confirm_cancel_sr` PATCHes platform → emits `response_ui.cancelled = true`.  
**Frontend still needed:** Add Cancel SR button (visible when `workflow_stage = "SR_CREATED"`); send `cancel_sr` action; handle `confirmation_status = "CANCEL_PENDING"` to show Confirm/Decline buttons; handle `response_ui.cancelled = true` to close form.

### ⚠️ Gap 2 — Unit Code not populated on form (frontend only)

**Current:** Agent stores `unit_codes` in `collected_data` and emits in confirmation card `fields[]`.  
**Frontend must:** Read `draft_preview.unit_codes` and bind to the Unit Code field. No agent change needed.

### ⚠️ Gap 3 — Title not shown on form (frontend only)

**Current:** Agent auto-generates `title` in `merge_state_node` and emits in `draft_preview.title` + confirmation card.  
**Frontend must:** Read `draft_preview.title` and populate the Title field. No agent change needed.

### ⚠️ Gap 4 — Notes not reflected on form (frontend only)

**Current:** Agent stores `notes` in `collected_data` and emits in `draft_preview.notes`.  
**Frontend must:** Read `draft_preview.notes` and update the Notes form field on each response. No agent change needed.

### ✅ Gap 5 — FM Update button remains after approval — FIXED (agent)

**Was:** Agent did not emit `stage_complete` after FM approval.  
**Fixed:** `fm_api_submission_node` now emits `response_ui = {stage_complete: true, action: "fm_approved"}` on approve, and `{stage_complete: false, action: "fm_saved"}` on save.  
**Frontend must:** Hide Update/Save buttons when `response_ui.stage_complete = true`.

### ⚠️ Gap 6 — RDD Confirm button not shown after upload (frontend)

**Current:** Agent emits `confirmation_status = "PENDING"` with confirmation card after upload.  
**Frontend must:** Check `ui.type = "confirmation_card"` to show Confirm button.

### ✅ Gap 7 — RDD Update/Final Approve after SR_COMPLETED — FIXED (agent)

**Was:** Agent emitted `workflow_stage = "SR_COMPLETED"` but no `workflow_complete` flag.  
**Fixed:** `rdd_api_submission_node` now emits `response_ui = {workflow_complete: true, action: "rdd_final_approved", workflow_stage: "SR_COMPLETED"}`.  
**Frontend must:** Hide all RDD action buttons when `response_ui.workflow_complete = true`.

### ✅ Gap 8 — confirmation_status not cleared after CREATE_SR — FIXED (agent)

**Was:** `api_submission_node` did not clear `confirmation_status` on success.  
**Fixed:** Now emits `confirmation_status: null, confirmation_required: false` alongside `response_ui.stage_complete = false`.

### ✅ Gap 9 — inspectionTime field — NEW (agent)

**New:** `inspectionTime` field (HH:MM) is now collected by the MM workflow.  
The agent asks for it after `endDate` and before `inspection_done_by`.  
The payload builder combines `startDate` + `inspectionTime` → UTC ISO for the API.  
**Frontend:** `draft_preview.inspectionTime` is now populated; show it in the confirmation card.

### ✅ Gap 10 — comments collected as a distinct field — FIXED (agent)

**Was:** `comments` was removed from `required_fields` and not proactively asked.  
**Fixed:** `comments` is now in `required_fields` for `CREATE_SR`, `FM_REVIEW`, and `RDD_REVIEW`. The bot asks for notes then comments as distinct turns.  
**Frontend:** Both `draft_preview.notes` and `draft_preview.comments` should be displayed in the confirmation card as separate rows.

### ✅ Gap 11 — RDDPM field order — FIXED (agent)

**Was:** RDD dates were asked before guideLineLink and rdd_upload_doc_type.  
**Fixed:** New order: `guideLineLink` → `rdd_upload_doc_type` → (document_status for Meeting Report) → dates.  
**Frontend:** No change needed — field order is agent-driven via `missing_fields`.

---

## 13. End-to-End Response Examples

### Example 1 — Confirmation card (CREATE_SR)

```json
{
  "message": "Here's a summary of your request. Please review and confirm.",
  "ui": {
    "type": "confirmation_card",
    "confirmAction": "confirm_create_sr",
    "cancelAction": "cancel_create_sr",
    "fields": [
      { "key": "title", "label": "Title", "value": "handover-t0105712-standard-fitout-inspection", "editable": false },
      { "key": "description", "label": "Description", "value": "Standard fit-out inspection for new tenant unit", "editable": true },
      { "key": "startDate", "label": "Start Date", "value": "2026-06-01", "editable": true },
      { "key": "endDate", "label": "End Date", "value": "2026-06-03", "editable": true },
      { "key": "inspection_done_by", "label": "Inspector", "value": "FM_MANAGER", "editable": true },
      { "key": "unit_codes", "label": "Unit Code", "value": "FF050", "editable": false },
      { "key": "notes", "label": "Notes", "value": "High priority", "editable": true }
    ]
  },
  "state": {
    "workflow_stage": "CREATE_SR",
    "status": "WAITING_FOR_USER",
    "confirmation_status": "PENDING",
    "ready_to_submit": true
  }
}
```

### Example 2 — SR submitted successfully

```json
{
  "message": "Your Handover Service Request has been successfully submitted. Your reference number is **SR-20260001**.",
  "ui": {
    "type": "message",
    "stage_complete": false
  },
  "state": {
    "workflow_stage": "SR_CREATED",
    "status": "SUBMITTED",
    "confirmation_status": null
  }
}
```

### Example 3 — FM review action chips

```json
{
  "message": "The FM review is ready. Please upload a supporting document and select an action.",
  "ui": { "type": "message" },
  "decorations": {
    "promote_preview": true,
    "suggestions": {
      "kind": "labeled_options",
      "options": [
        { "label": "Save Progress", "value": "save_fm_progress", "action": "save_fm_progress" }
      ]
    },
    "document_requirement": {
      "documents": [
        { "key": "SR_HANDOVER_CHECKLIST", "label": "Handover Checklist", "required": false, "status": "missing" },
        { "key": "SR_HANDOVER_SITE_SURVEY", "label": "Site Survey", "required": false, "status": "missing" },
        { "key": "SR_HANDOVER_OTHER", "label": "Other", "required": false, "status": "missing" }
      ],
      "uploadContext": { "session_id": "abc-123", "sr_id": "SR-20260001" }
    }
  },
  "state": {
    "workflow_stage": "FM_REVIEW",
    "status": "WAITING_FOR_USER",
    "confirmation_status": null
  }
}
```

### Example 4 — FM approved (stage_complete — requires Phase 2)

```json
{
  "message": "Your Handover Service Request has been approved at the FM review stage. Reference: **SR-20260001**. It has moved to RDD review.",
  "ui": {
    "type": "message",
    "stage_complete": true
  },
  "state": {
    "workflow_stage": "RDD_REVIEW",
    "status": "SUBMITTED",
    "confirmation_status": null
  }
}
```

### Example 5 — SR cancellation (requires Phase 1)

```json
{
  "message": "Your service request SR-20260001 has been cancelled.",
  "ui": {
    "type": "message",
    "cancelled": true
  },
  "state": {
    "workflow_stage": "CANCELLED",
    "status": "COMPLETED",
    "confirmation_status": null
  }
}
```

### Example 6 — RDD workflow complete

```json
{
  "message": "The RDD Handover Report has been approved. Your reference number is **SR-20260001**. The service request is now complete.",
  "ui": {
    "type": "message",
    "workflow_complete": true
  },
  "state": {
    "workflow_stage": "SR_COMPLETED",
    "status": "COMPLETED",
    "confirmation_status": null
  }
}
```

---

## 14. `decorations` Object Reference

The `decorations` object is emitted in review stages and during optional upload flows:

```typescript
interface ResponseDecorations {
  promote_preview?: boolean;             // True = render the SR preview card
  suggestions?: {
    kind: "labeled_options";
    options: Array<{
      label: string;                     // Button text
      value: string;                     // Internal value (also used as action)
      action: string;                    // SEND THIS as action_override — not value
    }>;
  };
  document_requirement?: {
    documents: Array<{
      key: string;                       // Document type ID
      label: string;                     // Human label
      required: boolean;
      status: "uploaded" | "missing";
    }>;
    uploadContext: {
      session_id: string;
      sr_id: string;
    };
    message?: string;                    // Optional instruction message
  };
}
```

> **Critical:** Use `options[].action` (not `options[].value`) as the `action_override` string. For these options they happen to be the same, but `action` is the authoritative field.

---

## 15. Versioning and Compatibility Notes

- This contract is **v1 (legacy Handover)**. The agent explicitly does not use `RE_EXAMINE` (V2 `UPDATE_APPROVE`) semantics — FM and RDD use legacy V1 status transitions only.
- The `response_ui` schema is currently loosely typed. Phase 3 work will formalise it as a Pydantic model.
- `corrected_fields` keys must exactly match the `collected_data` key names. Using platform API field names (e.g. `unitCode` instead of `unit_codes`) will silently fail.
- All date fields use ISO 8601 `YYYY-MM-DD` format. Do not send timestamps or locale-formatted dates.
