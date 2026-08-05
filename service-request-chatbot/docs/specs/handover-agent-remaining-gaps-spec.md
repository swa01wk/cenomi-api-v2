# Spec: Handover AI Agent — Remaining Functional Gaps

| Field | Value |
|-------|-------|
| **Status** | Implemented (agent-owned items) |
| **Date** | 2026-08-04 |
| **Branch** | `fix/handover-agent-remaining-gaps` |
| **Target** | `fix/staging` |
| **Module** | Service Request → Fit-out & Handover → Handover (AI Agent) |
| **Related** | [`../handover-workflow.md`](../handover-workflow.md), [`../uat-readiness.md`](../uat-readiness.md), [`../../changes.log`](../../changes.log) |

---

## 1. Purpose

During UAT of the AI-assisted Handover Service Request workflow, multiple defects were reported across conversation context, validation, form synchronization, and workflow completeness.

The **RDD PM happy-path submit** issue was already fixed and verified. This specification covers the remaining AI-specific defects: which ones the **agent/chatbot** can own, which require **MSP portal UI**, and which are blocked on **product / platform API** contracts.

This document is the authoritative design record for the agent-owned fixes shipped on `fix/handover-agent-remaining-gaps`.

---

## 2. Background

### 2.1 Architecture context

The chatbot runs a LangGraph pipeline (CREATE_SR → FM_REVIEW → RDD_REVIEW) that accumulates fields in `collected_data`, shows confirmation cards / draft previews in the **chat UI**, and submits to the Cenomi Service Request platform APIs.

The Tenant Portal also renders a classic Formik Service Request form (left side of UAT screenshots). The chatbot and the portal form are **separate UI surfaces**. Chat state does not automatically write into Formik unless the MSP host binds `draft_preview`.

### 2.2 Problem statement (UAT)

Observed defects included:

- Repeated / lost conversation context
- Invalid or ambiguous answers accepted
- Valid lease codes reported invalid (partially fixed earlier in PR 5051)
- Confirmed chat values not reflected on the portal form
- Document delete failing with invalid lease ID
- FM cancel appearing to reload the workflow
- Missing parity actions (Reject, Ask for More Info, Save as Draft)
- Mandatory **Handover Type** skipped
- Date / inspection assignee validation gaps

---

## 3. Goals and non-goals

### 3.1 Goals (this change set)

1. Revalidate lease codes when the user **intentionally** changes them after resolve.
2. Enforce inspection assignee as a single-select enum in chat confirmation UI.
3. Reject invalid inspection assignees (e.g. “mall manager”) at merge time.
4. Make document DELETE resilient when draft lacks numeric `lease_id`.
5. Soft-cancel FM review without wiping collected progress.
6. Document ownership of remaining gaps for portal / product teams.
7. Provide automated unit coverage for all agent-owned behaviours.

### 3.2 Non-goals (explicitly out of scope)

| Item | Why out of scope |
|------|------------------|
| Binding chat → left Formik form | Requires MSP portal UI integration |
| Mandatory Handover Type radios | No agent/API field contract today |
| Platform FM Reject PATCH | API contract not confirmed in chatbot |
| Ask for More Information | Product-blocked (`uat-readiness.md`) |
| CREATE Save as Draft | No DRAFT create path in agent; FM Save Progress ≠ CREATE draft |
| Fitout Confirmation agent | Separate feature branch / scope |

---

## 4. Defect inventory and ownership

| # | Defect | Owner | Status |
|---|--------|-------|--------|
| 1 | Context loss / repeat questions | Agent (prior) | Previously addressed; residual LLM risk |
| 2 | Wrong field mapping | Agent (prior) | Previously addressed via `active_field` remap |
| 3 | Restart after cancel | Agent (prior) | Previously addressed |
| 4 | Confirm before proceed | Agent (prior) | Previously addressed |
| 5 | Ambiguous / invalid answers | Agent | **This PR** — enum reject + higher confidence |
| 6 | Single-select inspection assignee | Agent + chat FE | **This PR** — emit `options` |
| 7 | Lease revalidation after change | Agent | **This PR** |
| 8 | Valid lease reported invalid | Agent (PR 5051) | Fixed earlier |
| 9 | Date year validation | Agent (prior) | Previously addressed |
| 10 | Chat ↔ portal Formik sync | **Portal UI** | Open |
| 11 | Doc delete invalid lease ID | Agent + chat FE | **This PR** |
| 12 | FM cancel reloads workflow | Agent (soft cancel) | **This PR** (agent); portal remount still portal |
| 13 | Submitted while portal still editable | **Portal UI** | Open |
| 14 | Reject / Ask more / CREATE draft | **Product + API** | Open |
| 15 | Handover Type mandatory | **Product + API + Portal** | Open |
| 16 | Date & Time collection | Agent (prior) | Previously addressed (`inspectionTime`) |

---

## 5. Functional requirements

### FR-1 — Lease code revalidation

**Given** a CREATE_SR draft with a resolved lease (`lease_id` present)  
**When** the user intentionally changes the lease code via:

- confirmation-card edit of `lease_code`, or
- `active_field == "lease_code"`, or
- explicit change phrasing (`change lease`, `switch lease`, `update to`, …)

**Then** the agent SHALL:

1. Update `collected_data.lease_code` to the new value.
2. Clear resolved enrichment: `lease_id`, `contract_id`, `brand_id`, `property_id`, `tenant_profile_id`, `unit_codes`, `city`, `contracted_area`, `mall`, `brand`, `lease`, `lease_brand_mall`.
3. Route to `lease_lookup` on the next merge routing step.
4. Revalidate eligibility (`not_found` / `handover_exists` / `activation_pending`).

**And** when the user message does **not** show intentional change, the agent SHALL NOT overwrite a resolved `lease_code` with an LLM hallucination.

### FR-2 — Inspection assignee single-select

**Given** a CREATE_SR confirmation card  
**When** the card is rendered  
**Then** `inspection_done_by` SHALL include:

```json
"options": ["FM_MANAGER", "OPERATIONS"]
```

so the chat frontend renders a `<select>` (already supported in `ServiceRequestSummaryCard`).

**And** merge SHALL:

- Accept `FM_MANAGER` / `OPERATIONS` (case/alias normalized, e.g. `ops` → `OPERATIONS`).
- Reject non-enum values (e.g. `mall manager`) without storing them.
- Require confidence ≥ `0.75` for this enum field.

### FR-3 — Document delete lease hydration

**Given** DELETE `/api/v1/upload` with `session_id` and/or `sr_id`  
**When** query `lease_id` is absent  
**Then** the backend SHALL resolve lease id in order:

1. Query param `lease_id` (if provided by FE).
2. Draft `collected_data.lease_id`.
3. Draft `collected_data.contract_id`.
4. Platform hydrate via GET `/service-requests/{sr_id}` (same helper as upload), including `contract_id` fallback.

**And** successful hydrate SHALL persist `lease_id` (and related refs) onto the draft for subsequent deletes.

**And** the chat FE SHALL pass `lease_id` or `contract_id` from `collectedData` when available.

### FR-4 — FM soft cancel

**Given** FM_REVIEW with staged action / pending confirmation  
**When** user cancels (`cancel`, `cancel_fm`, `cancel_update`)  
**Then** the agent SHALL:

1. Set `confirmation_status = REJECTED`.
2. Clear staged `backend_refs.fm_action`.
3. Return a WAITING message stating progress is still saved.
4. **Not** clear `collected_data`, documents, `sr_id`, or `workflow_stage`.

**And** `reject_fm_review` SHALL remain an explicit stub message until a platform reject PATCH contract exists (no fake success).

---

## 6. Technical design

### 6.1 Lease revalidation (`merge_state_node`)

```
extracted input / corrected_fields
        │
        ▼
_intentional_lease_code_change()?
   │ yes                         │ no
   ▼                             ▼
_clear_lease_resolution()     keep lease lock
set new lease_code            (_LEASE_CONFIRMED_FIELDS)
        │
        ▼
_route_after_merge → lease_lookup  (because lease_id absent)
```

Key symbols:

- `_LEASE_DERIVED_CLEAR_FIELDS`
- `_LEASE_CHANGE_PHRASES`
- `_intentional_lease_code_change()`
- `_clear_lease_resolution()`

Confirmation card: `lease_code` added to `_CREATE_SR_EDITABLE`.

### 6.2 Inspection assignee enum

| Layer | Behaviour |
|-------|-----------|
| `confirmation_node._ENUM_OPTIONS` | Emits select options |
| `merge_state_node._normalize_inspection_done_by` | Alias → enum; invalid → drop |
| `_ENUM_CONFIDENCE_THRESHOLD = 0.75` | Stricter than default `0.6` |
| `validation_service.validate_inspection_done_by` | Existing backend validation (unchanged contract) |

### 6.3 Delete hydrate (`upload.py`)

Delete path mirrors upload:

1. Load draft refs from `collected_data`.
2. If no lease and `sr_id` present → `_hydrate_backend_refs_from_platform`.
3. Persist hydrated refs onto draft.
4. Call `platform.delete_document(..., lease_id=resolved_lease)`.

FE:

- `deleteDocument(documentId, sessionId, srId?, leaseId?)`
- `DocumentUploadPanel` passes `collectedData.lease_id || collectedData.contract_id`

### 6.4 FM entry cancel

Unified handlers for `cancel` / `cancel_fm` / `cancel_update`:

- Soft cancel only (confirmation + `fm_action`).
- Reject stub messaging updated to be honest about platform Action menu.

---

## 7. API / UI contracts

### 7.1 Chat → portal (existing, consumed by MSP)

Agent already emits per-turn:

- `draft_preview` / confirmation card fields
- `stage_complete` / `workflow_complete` (where applicable)

**Portal must bind these** for left-form sync and edit locking. This PR does not change that contract; it documents the dependency.

### 7.2 Confirmation field shape (extended)

```ts
type ConfirmationField = {
  key: string;
  label: string;
  value: unknown;
  editable: boolean;
  options?: string[]; // NEW usage for inspection_done_by
};
```

### 7.3 DELETE upload query params

| Param | Required | Notes |
|-------|----------|-------|
| `document_id` | Yes | Platform document id |
| `session_id` | Recommended | Enables draft resolve + hydrate persist |
| `sr_id` | Recommended for FM/RDD | Hydrate source when lease missing |
| `lease_id` | Optional | Preferred when FE already knows it |

---

## 8. Acceptance criteria

### Agent / chatbot

- [x] Changing lease after resolve with “change lease to X” clears `lease_id` and re-enters lookup.
- [x] Casual LLM lease hallucinations after resolve do not overwrite `lease_code`.
- [x] Confirmation card shows Inspection Done By as a dropdown with two options.
- [x] “mall manager” is not stored as `inspection_done_by`.
- [x] “ops” / “Operations” normalize to `OPERATIONS`.
- [x] DELETE works when draft only has `contract_id` or when hydrate fills `lease_id`.
- [x] FE delete passes lease id when present in collected data.
- [x] FM cancel keeps unit readiness / docs; does not restart CREATE_SR.
- [x] Reject via AI states that portal Action menu must be used (no false rejection).

### Portal / product (not this PR)

- [ ] Formik binds `draft_preview` for lease, title, units, notes, dates, handover type.
- [ ] Form locks when chat reports submitted / stage complete.
- [ ] Handover Type field + API key defined and collected by agent.
- [ ] FM Reject / Ask for More Info / CREATE draft platform contracts defined.

---

## 9. Test plan

### 9.1 Automated (shipped)

| Suite | Cases |
|-------|-------|
| `tests/unit/test_merge_state_node.py` | Lease lock vs intentional change; assignee reject/normalize; `_route_after_merge` |
| `tests/unit/test_confirmation_node.py` | Editable `lease_code`; `inspection_done_by.options` |
| `tests/unit/test_fm_nodes.py` | Soft cancel; reject stub |
| `tests/unit/test_upload_delete_hydrate.py` | Explicit lease, draft lease, contract_id, platform hydrate, platform failure, draft cleanup |

Command:

```bash
cd service-request-chatbot/backend
.venv/bin/python -m pytest \
  tests/unit/test_merge_state_node.py \
  tests/unit/test_confirmation_node.py \
  tests/unit/test_fm_nodes.py \
  tests/unit/test_upload_delete_hydrate.py -q
```

### 9.2 Manual UAT smoke

1. CREATE_SR → resolve lease → say “change lease to \<other\>” → expect re-lookup / eligibility message.
2. Reach confirmation → Inspection Done By is a select; pick Operations → confirm.
3. Type “mall manager” when asked who performs inspection → agent re-asks / does not accept.
4. FM upload a doc → delete it with a draft that previously lacked `lease_id` → success.
5. FM fill dates → open confirmation → Cancel → dates/docs still present; no CREATE restart.

---

## 10. Files touched

| Path | Change |
|------|--------|
| `backend/app/agents/graph/nodes/shared/merge_state_node.py` | Lease revalidation + enum merge guards |
| `backend/app/agents/graph/nodes/handover/confirmation_node.py` | Editable lease_code; enum options |
| `backend/app/agents/graph/nodes/handover/fm_review_entry_node.py` | Soft cancel; reject stub |
| `backend/app/api/routes/upload.py` | DELETE lease hydrate |
| `frontend/lib/api/upload-client.ts` | Optional `leaseId` on delete |
| `frontend/components/chatbot/DocumentUploadPanel.tsx` | Pass lease/contract id |
| `backend/tests/unit/test_*.py` | Coverage updates + new delete suite |
| `changes.log` | Change summary |
| `docs/specs/handover-agent-remaining-gaps-spec.md` | This specification |

---

## 11. Handoff notes for UI/UX (MSP portal)

Please implement:

1. **Bind `draft_preview` → Formik** for fields the agent collects (lease, units, title, description, dates, inspection assignee, notes/comments, FM/RDD dates).
2. **Disable / lock the form** when the agent returns `stage_complete`, `workflow_complete`, or cancelled — avoid showing editable Update while chat says Submitted.
3. Keep **Handover Type** radios portal-owned until product publishes the API field name and values; then bind from chat once the agent collects them.
4. Keep **Reject / Ask for More Info / CREATE Save as Draft** in the portal Action menu until platform contracts are confirmed for the chatbot.

---

## 12. Open questions (product / platform)

1. What is the CREATE payload field for Handover Type (`Already Trading` / `Initiate Handover`)? Allowed values?
2. What is the FM Reject PATCH body and resulting operation status?
3. What are Ask-for-More-Info semantics (status, assignee, comment required)?
4. Can CREATE POST with a DRAFT/IN_PROGRESS status, or only full submit?

Until these are answered, the agent must not fake success for those actions.

---

## 13. Rollout

1. Merge this branch into `fix/staging`.
2. Deploy chatbot staging with existing MSP integration env vars.
3. Run §9.2 smoke on UAT against chatbot staging.
4. Track portal Formik binding as a separate MSP frontend ticket using §11.
5. Track product API questions as a separate backlog item using §12.

---

## 14. Revision history

| Date | Author | Notes |
|------|--------|-------|
| 2026-08-04 | Chatbot eng | Initial spec for agent-owned remaining gaps |
| 2026-08-05 | Chatbot eng | Spec committed for PR merge into `fix/staging` |
