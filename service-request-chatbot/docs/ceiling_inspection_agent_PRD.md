# Ceiling Inspection Agent — Product Requirements Document

**Status:** For senior review
**Date:** 24 September 2026
**Version:** 2.0
**Agent:** `rdd_ceiling_inspection_agent`
**Service requests:** Fit-out and Handover / Open Ceiling Inspection, and Fit-out and Handover / Close Ceiling Inspection
**Source of truth:** Service Request Workflow Specification, Open Ceiling Inspection, v1.0, 11 September 2026 (section 6.5.6), and Close Ceiling Inspection, v1.0, 11 September 2026 (section 6.5.1)

---

## 1. Executive Summary

One agent, `rdd_ceiling_inspection_agent`, is added to the existing LangGraph Help Agent, at the same level as `rdd_handover_agent` and `rdd_fitout_start_agent`. It covers two service requests under category `FIT_OUT_AND_HANDOVER`:

- `OPEN_CEILING_INSPECTION`
- `CLOSE_CEILING_INSPECTION`

The two inspections are the same flow. The 11 September 2026 workflow PDFs say each shares its exact approval flow and levels with the other. cenomi-api already handles them in one branch. The chatbot does the same. The user’s words choose the subcategory. After that, create, review, approval, and tenant acknowledgement are one path.

The agent is a peer. It is not a branch inside the handover agent. POI Architectural and POI MEP stay out of this build.

Phase 1 is the full lifecycle in both 11 September 2026 workflows:

- A Mall Manager, FM Manager or RDD Project Manager raises the request in chat (section 3.2). The creator's own approval level is auto-approved by the platform: RDD PM's level 1 at create, when RDD PM uploads the draft report and its status during create; FM Manager's level 2 once level 1 is approved. Tenants do not create; they acknowledge.
- Level 1 is the RDD Project Manager. In the API that role is `DD_ENGINEER` (role id 12). They upload the draft report, then approve, ask for more information, or assign the request on.
- Level 2 is the FM Manager (`FM_MANAGER`, role id 20). They approve only after the report exists, and can ask for more information or assign the request on.
- After both levels finish, the service request becomes `REPORT_SUBMITTED`. The tenant acknowledges the report, and the request is complete. Acknowledgement is the tenant's only step after create.

There is no Reject action on either approval level. The platform already accepts both service requests. This work is the chatbot, plus the existing frontend create-page redirect. No change is required in `cenomi-api`.

---

## 2. Why one agent

Open Ceiling (document 16 of 76, section 6.5.6) and Close Ceiling (document 11 of 76, section 6.5.1) were compared line by line. After the name of the inspection is set aside, the approval text matches.

Both PDFs state this chain, and each names the other as the shared flow:

1. The tenant submits the request.
2. Level 1, the RDD Project Manager, reviews it.
3. Level 2, the FM Manager, reviews it.
4. The tenant acknowledges the submitted report.

Approvers are the same on both:

| Level | Workflow PDF | API role | Role id |
|---|---|---|---|
| 1 | RDD Project Manager | `DD_ENGINEER` | 12 |
| 2 | FM Manager | `FM_MANAGER` | 20 |

There is no role code `RDD_PROJECT_MANAGER`. The workflow documents use the display name “RDD Project Manager”. The API and the roles fixture use `DD_ENGINEER`.

cenomi-api already treats the pair as one path. Level 2 approval sets `REPORT_SUBMITTED` for both subcategories in one condition, and both use `notifyActionOnHandoverInspectionSR`. The code comment on that path is `RDDPM -> FM`.

They were inserted on one shared workflow row, together with the two pre-opening inspections. A later write to that row changes all of them. See section 2.1.

```text
Mall Manager / FM Manager / RDD PM (creators)
        |
        v
POST /service-requests
sub_category = OPEN_CEILING_INSPECTION or CLOSE_CEILING_INSPECTION
status = IN_PROCESS
        |
        v
Level 1  DD_ENGINEER
Upload the draft report for that subcategory
Approve / Ask for more info / Assign To
        |
        |  Approve keeps the SR as IN_PROCESS
        v
Level 2  FM_MANAGER
Approve only if the draft report exists
Ask for more info / Assign To
        |
        |  Approve sets REPORT_SUBMITTED
        v
Tenant acknowledges the report
        |
        v
Request complete
```

### Platform facts that stay, because they do not change the PDF steps

| Item | Value |
|---|---|
| Category | `FIT_OUT_AND_HANDOVER` |
| Assignment | Workflow |
| Who creates | Mall Manager, FM Manager, RDD PM (section 3.2). `CAN_RAISE_HANDOVER_SR` alone is not enough. |
| Level 1 role code | `DD_ENGINEER` (role id 12) |
| Level 1 name in the workflow PDF | RDD Project Manager |
| Level 1 name in the roles fixture | DD Engineer |
| Level 2 role code | `FM_MANAGER` (role id 20) |
| Inspection done by | Hardcoded `12`. Never asked. |
| Create status | `IN_PROCESS` |
| Status after level 1 approves | `IN_PROCESS` |
| Status after level 2 approves | `REPORT_SUBMITTED` |
| Draft report statuses | `APPROVED`, `APPROVED_WITH_CHANGES` |
| Disapproval status | `DISAPPROVED` on the rejected-report document |
| Other attachment | `FITOUT_INSPECTION_OTHER` |

A create posted as `PENDING` does not open the approval operations. Handover create already posts `IN_PROCESS`. This agent does the same.

### 2.1 Subcategory map

This is the only product difference. One map supplies these values. The rest of the agent reads the map. It does not branch the conversation.

| | Open Ceiling | Close Ceiling |
|---|---|---|
| Sub-category | `OPEN_CEILING_INSPECTION` | `CLOSE_CEILING_INSPECTION` |
| Draft report | `FITOUT_OCI_REPORT_DRAFT` | `FITOUT_CCI_REPORT_DRAFT` |
| Disapproval file | `SR_REJECTED_OCI_REPORT` | `SR_REJECTED_CCI_REPORT` |
| SharePoint folder | `OCI` | `CCI` |
| Spoken name | Open Ceiling Inspection | Close Ceiling Inspection |

The draft report is uploaded by `DD_ENGINEER` only. Its statuses are `APPROVED` and `APPROVED_WITH_CHANGES`. The disapproval file is uploaded by `DD_ENGINEER` only, with status `DISAPPROVED`.

The API form JSON for both subcategories is the same shape, including `document_status_map`. The frontend skeleton for Close Ceiling has drifted: it also lists `DISAPPROVED` on the draft report. This agent follows the API form. Disapproval stays on the rejected-report document for both.

SharePoint folder names come from the existing document usecase. The agent does not create folders. It only names the document type the platform already files under `OCI` or `CCI`.

### Create fields

Taken from both form skeletons. The fields match.

| Field | Required | Who fills it |
|---|---|---|
| Tenant | Yes | From the lease (`company_name` = the lease's tenant_profile_id). Admins raise the request for the lease's tenant. |
| Lease, brand, mall | Yes | The user. Lookup fills unit codes, city, area, brand, and tenant profile. |
| City | No | Filled from the lease. Read only. |
| Contracted area | No | Filled from the lease. Read only. |
| Title | Yes | The user |
| Description | Yes | The user |
| Start date | Yes | The user |
| End date | Yes. Must be on or after the start date. | The user |
| Inspection done by | Yes | Seeded as `12`. Not asked. |
| Notes | No | The user |
| Comments | Yes | The user |
| Other documents | No | The user. Type `FITOUT_INSPECTION_OTHER`. |

Dates are stored as ISO. A user may type `DD/MM/YYYY`. The agent converts that before submit.

### Lease eligibility

Open and Close Ceiling Inspection can be raised at any time. There is no fit-out date requirement: a lease does not need `actual_fitout_start_date` or any other milestone date. The agent sends lease lookup `purpose=ceiling_inspection`, which cenomi-api answers with every lease the caller can access: no "no existing handover SR" gate and no milestone filter (the earlier `purpose=fitout_confirm` also required `actual_shop_handover_date`). The usual checks still apply: property scope, lease activation, and the SR's own end date not being before its start date.

### Known data conflict, and how this agent treats it

One workflow row was originally mapped to all four inspections (Close Ceiling, Open Ceiling, POI MEP, POI Architectural). The only later flow write in this repo selects that row through Open Ceiling and sets level 1 to `FM_MANAGER` and level 2 to `DD_ENGINEER`. That is the reverse of both PDFs. Because the row is shared, Close Ceiling is on the same write. There is no later migration in this repo that sets level 2 to Mall Manager. A live database can still differ.

This agent follows the PDFs: level 1 `DD_ENGINEER`, level 2 `FM_MANAGER`. If an environment still has the reverse order, or Mall Manager on level 2, that row is corrected in data before review is tested there. The agent design does not change to match the stale row. The correction applies to both subcategories, because they share the row.

---

## 2.5 Approval Behaviour

`APPROVED` on a level is that approver’s action. It is not the final service-request status. The actions are the same for both subcategories.

| Level | Role | Actions the agent offers | What happens on approve |
|---|---|---|---|
| 1 | `DD_ENGINEER` | Approve. Ask for more information, only when this user did not create the request. Assign To, when the report is missing. | The level-1 operation is approved. The service request stays `IN_PROCESS`. FM Manager is next. |
| 2 | `FM_MANAGER` | Approve, only after the draft report for this subcategory exists. Ask for more information. Assign To, when documents are missing. | The admin chain ends. The API sets the service request to `REPORT_SUBMITTED`. |

Ask for more information sets `ACTION_REQUIRED`. When the information comes back, the same level resumes.

Assign To sets `REASSIGN_TO`. The acting approver picks the person at runtime. The workflow PDFs do not fix a list of target roles, so the agent does not invent one.

Reject is not offered. The “Reject” mark on each PDF summary table is ignored. The level-by-level section of each PDF is the one we follow: Reject is unavailable.

Request re-upload is not applicable for either sub-category.

### Status list the agent must understand

From both workflow PDFs, and confirmed in the API for the steps this agent uses:

| Status | Meaning in this flow |
|---|---|
| `IN_PROCESS` | Created, and still inside the admin chain. This includes the state after level 1 approves. |
| `ACTION_REQUIRED` | An approver asked for more information. The same level resumes when it comes back. |
| `REASSIGN_TO` | An approver assigned the pending operation to someone else. |
| `REPORT_SUBMITTED` | Both admin levels are finished. The tenant can acknowledge the report. |
| `CLOSED` | Finished after tenant acknowledgement. |
| `CANCELLED` | Cancelled from the special footer actions where the platform already allows it. |

`REJECTED` exists on the platform status list. This agent does not set it. Disapproval of the inspection is a document, the rejected-report type for that subcategory, with status `DISAPPROVED`, uploaded by `DD_ENGINEER`. It is not a workflow Reject button.

### API behaviour this agent relies on

| Moment | What cenomi-api does |
|---|---|
| Create | Notifies `DD_ENGINEER` in the app and by email. |
| Level 1 approve | Service request stays `IN_PROCESS` until every workflow operation is finished. |
| Level 2 approve | Sets the operation and the service request to `REPORT_SUBMITTED` for Open Ceiling and Close Ceiling. |
| After `REPORT_SUBMITTED` | The tenant acknowledges the report. That acknowledgement completes the service request. |

The agent calls the existing create and update APIs. It does not add a new platform permission.

---

## 2.6 Documents

Uploads use the existing chat upload route. Allowed types stay PDF, JPEG, and PNG.

The document type depends on the subcategory already chosen. See the map in section 2.1.

| Document type | Who can attach it | Statuses |
|---|---|---|
| `FITOUT_INSPECTION_OTHER` | Creator, Mall Manager, DD Engineer, FM Manager. The form also lists Service Provider and Regional DD Engineer for this type only. | None |
| Draft report for this subcategory | `DD_ENGINEER` only | `APPROVED`, `APPROVED_WITH_CHANGES` |
| Rejected report for this subcategory | `DD_ENGINEER` only | `DISAPPROVED` |

Level 2 cannot approve until the draft report for this subcategory is on the request. FM Manager may attach `FITOUT_INSPECTION_OTHER`. FM Manager does not upload the inspection report.

`document_status_map` on the update payload is copied from one real web-form submission of the same subcategory before the review payload is coded. The migrations do not fix that JSON shape. Open Ceiling and Close Ceiling use the same shape.

---

## 2.7 Notifications

These already exist in cenomi-api. The agent does not send them itself.

| When | Who is notified | Channel |
|---|---|---|
| Request created | `DD_ENGINEER` | In-app and email |
| An approver acts | The next party in the inspection notification path already used for Open Ceiling and Close Ceiling | Existing `notifyActionOnHandoverInspectionSR` |

The agent’s success message after create names the inspection and says the request is with the RDD Project Manager. After level 1 it says the request is with the FM Manager and still in process. After level 2 it says the report is submitted and the tenant can acknowledge it.

---

## 3. Agent Architecture

### 3.1 Intent taxonomy

Four action intents are added. “Raise a handover” stays on `CREATE_RDD_SERVICE_REQUEST`. A plain status question stays on the existing read intent `CHECK_SERVICE_REQUEST_STATUS`.

The intent does not split Open Ceiling from Close Ceiling. The utterance does. The entry node sets `sub_category` from the words below. If those words are missing, it asks once which inspection, then continues on the same path.

| Intent | Who | Trigger |
|---|---|---|
| `CREATE_CEILING_INSPECTION_SR` | Mall Manager, FM Manager, RDD PM (section 3.2) | “raise an OCI”, “open ceiling inspection”, “close ceiling”, “CCI for lease …”, “ceiling inspection” |
| `REVIEW_CEILING_INSPECTION_SR` | `DD_ENGINEER`, when the live operation is assigned to them | Open either inspection at level 1, upload the report, approve, ask for more information, or assign |
| `APPROVE_CEILING_INSPECTION_SR` | `FM_MANAGER`, when the live operation is assigned to them | Open either inspection at level 2 and approve, ask for more information, or assign |
| `ACKNOWLEDGE_CEILING_INSPECTION_SR` | Tenant roles only, on a request in `REPORT_SUBMITTED` | Acknowledge the submitted report |

Subcategory from the utterance:

| Words | Sub-category |
|---|---|
| “open ceiling”, “open ceiling inspection”, “OCI” | `OPEN_CEILING_INSPECTION` |
| “close ceiling”, “close ceiling inspection”, “CCI” | `CLOSE_CEILING_INSPECTION` |
| “ceiling inspection” with neither of the above | Ask which one. Do not guess. |

On review, approve, and acknowledge, the subcategory is read from the existing service request. The user does not choose it again.

Phrases that must not be stolen from handover: “raise a handover”, “handover SR”, “unit handover”. Phrases that must come here: “open ceiling”, “close ceiling”, “OCI”, “CCI”, “ceiling inspection”.

### 3.2 RBAC

Mall Manager, FM Manager and the RDD Project Manager create a ceiling inspection; only the tenant acknowledges the submitted report. Review is gated by the role on the live workflow operation, not by a chatbot-only permission string. The gates are the same for both subcategories.

`CAN_RAISE_HANDOVER_SR` is not the create gate for this agent: RDD Contractor holds it too and must not create. A token that carries the permission without a creator role is refused.

`CAN_RDD_REVIEW_HANDOVER_SR` is a chatbot string. It is not issued by cenomi-api. This agent does not use it.

**Who can create and acknowledge**

Create: `CREATE_ROLES` in `ceiling_schema.py` — Mall Manager (11), DD Engineer / RDD PM (12), FM Manager (20). Acknowledge: `ACK_ROLES` = `TENANT_ROLES` — every role the roles fixture marks `user_type: TENANT`, plus `SHOP_MANAGER`, the role name live AI-session tokens carry for the Shop Manager / Company Admin tenant user.

| Role | Id | Create | Acknowledge |
|---|---|---|---|
| Mall Manager | 11 | Yes | No |
| DD Engineer (RDD PM) | 12 | Yes — uploads the draft report + status during create | No |
| FM Manager | 20 | Yes | No |
| Primary Tenant Contact | 2 | No | Yes |
| Authorized Signatory | 3 | No | Yes |
| Billing Contact | 4 | No | Yes |
| Financial Controller | 6 | No | Yes |
| Company Admin Central | 7 | No | Yes |
| Leasing Manager | 29 | No | Yes |
| Company Admin | 72 | No | Yes |
| Service Provider | 73 | No | Yes |
| Marketing Contact | 78 | No | Yes |
| Shop Manager | — (token alias) | No | Yes |

Super Admin (21), Admin (22) and RDD Contractor (91) are refused at create. The refusal names the three creator roles.

**Auto-approval of the creator's level (cenomi-api).** The platform auto-approves any workflow level assigned to one of the creator's roles, and never approves RDD PM's level 1 without the draft report (payload `documents` + `document_status_map` APPROVED / APPROVED_WITH_CHANGES, or the draft report among the SR's attachments):
- RDD PM creates with the report → level 1 approved at create, FM Manager notified.
- FM Manager creates → level 1 stays with RDD PM (report required to approve) → level 2 auto-approved → `REPORT_SUBMITTED`.
- Mall Manager creates → no auto-approval.

A user whose role cannot be resolved is not refused by the chatbot, matching every other entry guard. The platform still checks the bearer token on submit.

**Who can act after create**

| Role | Level 1 | Level 2 | Acknowledge |
|---|---|---|---|
| `DD_ENGINEER` | Yes, when the operation is assigned to them | No | No |
| `FM_MANAGER` | No | Yes, when the operation is assigned to them | No |
| Tenant roles | No | No | Yes, when status is `REPORT_SUBMITTED` |
| `ADMIN` | Yes (override) | Yes (override) | No |

Both workflow PDFs say the creator cannot use Ask for more information at level 1. With tenant-only create, the level-1 `DD_ENGINEER` is never the creator; the agent keeps the check as a safeguard. Approve at level 2 stays blocked until the draft report for that subcategory exists.

The bot is not stricter than the web form. It does not require an earlier inspection before either request can be raised. The platform does not enforce that order.

### 3.3 Node architecture

New package `backend/app/agents/graph/nodes/ceiling/`. Shared nodes stay shared: load session, status sync, field extraction, merge, lease lookup, validation, missing field, response, and save.

There is one graph. The subcategory map is read inside the payload builder and the review entry. It is not a second graph.

```text
Supervisor
    |
    +-- CREATE_CEILING_INSPECTION_SR --> ceiling_entry
    |                                      |
    |                                      v
    |                            field extraction, merge,
    |                            lease lookup, validation
    |                                      |
    |                                      v
    |                             ceiling_confirmation
    |                                      |
    |                                      v
    |                           ceiling_payload_builder
    |                                      |
    |                                      v
    |                           ceiling_api_submission
    |                              POST /service-requests
    |
    +-- REVIEW_CEILING_INSPECTION_SR --> ceiling_review_entry
    |   APPROVE_CEILING_INSPECTION_SR        |
    |                                        v
    |                              report upload and decision
    |                                        |
    |                                        v
    |                             ceiling_review_confirmation
    |                                        |
    |                                        v
    |                             PATCH /service-requests/{id}
    |
    +-- ACKNOWLEDGE_CEILING_INSPECTION_SR --> ceiling_ack_entry
                                                 |
                                                 v
                                      PATCH tenant acknowledgement
```

Registering the workflow config alone is not enough. Several shared nodes list agent names by hand. Those lists must include `rdd_ceiling_inspection_agent`, or an existing ceiling inspection will reopen as a handover.

### 3.4 Node responsibilities

| Node | LLM | Responsibility |
|---|---|---|
| `ceiling_entry` | No | Set `rdd_ceiling_inspection_agent` and `CREATE_CEILING_INSPECTION_SR`. Set `sub_category` from the utterance. If it is neither open nor close, ask which inspection and wait. Seed `inspection_done_by = 12`. Refuse the turn if the user is not in a tenant role. |
| `ceiling_confirmation` | No | Show the inspection name, lease, title, description, dates, comments, optional notes, and other attachments. Confirm, edit, or cancel. |
| `ceiling_payload_builder` | No | Build the create body from the subcategory map. `status = IN_PROCESS`. Do not send handover-only fields. |
| `ceiling_api_submission` | No | `POST /service-requests`. Report the new id, or the platform error. |
| `ceiling_review_entry` | No | Read the synced operation and the subcategory already on the request. Level 1 is `DD_ENGINEER`. Level 2 is `FM_MANAGER`. Hide Ask for more information at level 1 when the actor created the request. Hide Approve at level 2 until the draft report for this subcategory exists. |
| `ceiling_review_confirmation` | No | Confirm the document, its status, the comment, and the chosen action before the update is sent. |
| `ceiling_ack_entry` | No | Tenant acknowledgement when the status is `REPORT_SUBMITTED`. Any non-tenant role, `ADMIN` included, is told the request is waiting for the tenant. |

### 3.5 Design principles

1. The agent is thin. Shared pipeline nodes do the extraction, merge, lease lookup, and validation.
2. The stage comes from a platform status sync, not from chat memory.
3. Lease-derived fields are never asked. One user answer fills one field. Start date and end date are separate questions.
4. `inspection_done_by` is always 12. The agent never asks who will do the inspection.
5. The bot matches the web form. It does not add a prerequisite that an earlier inspection must exist first.
6. Reject is not a conversation path.
7. The agent does not invent Assign To target roles.
8. Open Ceiling and Close Ceiling do not get separate nodes. The subcategory map is the only fork.

---

## 4. Create Payload (Key Decision)

The closest existing pattern is fit-out confirmation: its own agent name, its own intents, and the shared field pipeline. The payload follows the ceiling inspection form, not the handover form. The subcategory on the payload is the one chosen in section 3.1.

The create body includes:

- `service_category = FIT_OUT_AND_HANDOVER`
- `sub_category` from the map in section 2.1
- `status = IN_PROCESS`
- lease, brand, mall, unit codes, city, contracted area, tenant profile
- title, description, start date, end date, notes, comments
- `inspection_done_by = 12` and the same value under the camel-case key the form posts

The create body does not include handover-only keys such as `already_handovered`, `unit_readiness_date`, `expected_handover_date`, or `guideLineLink`.

End date must be on or after start date. The agent asks again if it is not.

---

## 5. Conversation Flows

### 5.1 Create, subcategory named

```text
User: Raise an OCI for lease t0105712
Bot:  Lease resolved. What is the title?
User: Ceiling services inspection
Bot:  What is the scope?
User: New shopfront, service corridor B
Bot:  When should it start?
User: 02/10/2026
Bot:  When should it end?
User: 03/10/2026
Bot:  Any comments or access instructions?
User: Access via service corridor B
Bot:  Confirmation card
User: Confirm
Bot:  Open Ceiling Inspection created. It is with the RDD Project Manager.
```

“Close ceiling” or “CCI” follows the same questions. The confirmation card and the success line say Close Ceiling Inspection, and the payload uses `CLOSE_CEILING_INSPECTION`.

The confirmation card shows the inspection name, lease, title, description, start, end, comments, and inspection done by as RDD Project Manager. It does not ask the user to pick that role.

### 5.2 Create, subcategory not named

```text
User: Raise a ceiling inspection for lease t0105712
Bot:  Is this an Open Ceiling Inspection or a Close Ceiling Inspection?
User: Close ceiling
Bot:  Lease resolved. What is the title?
```

The agent asks this once. It does not default to Open Ceiling.

### 5.3 Level 1

```text
User: I've finished the open ceiling inspection for SR-104233
Bot:  Upload the Open Ceiling report.
User: uploads FITOUT_OCI_REPORT_DRAFT
Bot:  Approved, or approved with changes?
User: Approved with changes
Bot:  Comment for the changes.
User: Sprinkler spacing in zone 2
Bot:  Confirmation card
User: Confirm
Bot:  Sent to the FM Manager. The request stays in process.
```

A Close Ceiling review uploads `FITOUT_CCI_REPORT_DRAFT`. The questions are the same.

Ask for more information is hidden if the level-1 user is also the creator. With tenant-only create this does not occur; the check stays as a safeguard.

### 5.4 Level 2

```text
User: Approve the close ceiling inspection SR-104233
Bot:  The Close Ceiling report is on file. Confirm approval.
User: Confirm
Bot:  Report submitted. The tenant can now acknowledge it.
```

If the draft report for that subcategory is missing:

```text
Bot:  This request cannot be approved yet.
      The RDD Project Manager still needs to upload the Close Ceiling report.
```

Approve is not offered in that case. The same block applies to Open Ceiling, naming that report.

### 5.5 Tenant acknowledgement

```text
User: Acknowledge the OCI report for SR-104233
Bot:  The report is submitted. Confirm acknowledgement.
User: Confirm
Bot:  Open Ceiling Inspection SR-104233 is complete.
```

The completion line uses the subcategory already on the request.

---

## 6. State

`active_agent` is `rdd_ceiling_inspection_agent`. `service_category` is `FIT_OUT_AND_HANDOVER`. `sub_category` is `OPEN_CEILING_INSPECTION` or `CLOSE_CEILING_INSPECTION`.

| Stage | Meaning |
|---|---|
| `CREATE_CEILING_INSPECTION_SR` | Collecting the create form |
| `CEILING_L1_REVIEW` | Waiting on `DD_ENGINEER` |
| `CEILING_L2_REVIEW` | Waiting on `FM_MANAGER` |
| `CEILING_REPORT_SUBMITTED` | Waiting on the tenant |
| `CEILING_COMPLETED` | Finished |
| `CANCELLED` | The user or the platform cancelled |

Collected data holds the subcategory, the lease fields, title, description, `startDate`, `endDate`, notes, comments, `inspection_done_by = 12`, and, on review, the document type and document status. Document type codes are looked up from the subcategory. They are not a second set of stages.

---

## 7. WorkflowConfig

```python
WorkflowConfig(
    agent_name="rdd_ceiling_inspection_agent",
    stage_sync_nodes={
        "CEILING_L1_REVIEW": "ceiling_review_entry",
        "CEILING_L2_REVIEW": "ceiling_review_entry",
        "CEILING_REPORT_SUBMITTED": "ceiling_ack_entry",
    },
    collection_stages=frozenset({
        "CREATE_CEILING_INSPECTION_SR",
        "CEILING_L1_REVIEW",
        "CEILING_L2_REVIEW",
    }),
    confirmation_nodes={
        "CREATE_CEILING_INSPECTION_SR": "ceiling_confirmation",
        "CEILING_L1_REVIEW": "ceiling_review_confirmation",
        "CEILING_L2_REVIEW": "ceiling_review_confirmation",
    },
    terminal_stages=frozenset({"CEILING_COMPLETED", "CANCELLED"}),
    action_intents=frozenset({
        "CREATE_CEILING_INSPECTION_SR",
        "REVIEW_CEILING_INSPECTION_SR",
        "APPROVE_CEILING_INSPECTION_SR",
        "ACKNOWLEDGE_CEILING_INSPECTION_SR",
    }),
)
```

In the service-request registry, both `FIT_OUT_AND_HANDOVER` / `OPEN_CEILING_INSPECTION` and `FIT_OUT_AND_HANDOVER` / `CLOSE_CEILING_INSPECTION` map to `rdd_ceiling_inspection_agent`, display name “RDD Ceiling Inspection Agent”.

The frontend create map is one agent to one subcategory today. This agent cannot use that shape. The redirect reads the subcategory already on the turn and opens the existing Open Ceiling form or the existing Close Ceiling form. If the subcategory is not chosen yet, the chat stays put and asks which inspection.

`CREATE_CEILING_INSPECTION_SR` is added to the create-stage set that allows that redirect. `CEILING_L1_REVIEW`, `CEILING_L2_REVIEW`, and `CEILING_REPORT_SUBMITTED` are non-create stages, so a reviewer is not sent to the create page.

---

## 8. Platform API

Reuse the existing service-request client. No new cenomi-api endpoint and no new API client file.

| Call | When |
|---|---|
| `POST /service-requests` | Create. `status = IN_PROCESS`. `sub_category` from the map in section 2.1. |
| `GET /service-requests/{id}` | Status sync. Map the in-progress operation to level 1 or level 2 by workflow level and assigned role. Read `sub_category` from the request. |
| `PATCH /service-requests/{id}` | Level 1 decision, level 2 decision, and tenant acknowledgement. |
| Existing upload route | Draft report, rejected report, and other attachments. The document type comes from the subcategory map. |

---

## 9. Files to Create / Modify

### New files (AI backend)

- `backend/app/agents/graph/nodes/ceiling/__init__.py`
- `backend/app/agents/graph/nodes/ceiling/ceiling_entry_node.py`
- `backend/app/agents/graph/nodes/ceiling/ceiling_confirmation_node.py`
- `backend/app/agents/graph/nodes/ceiling/ceiling_payload_builder_node.py`
- `backend/app/agents/graph/nodes/ceiling/ceiling_api_submission_node.py`
- `backend/app/agents/graph/nodes/ceiling/ceiling_review_entry_node.py`
- `backend/app/agents/graph/nodes/ceiling/ceiling_review_confirmation_node.py`
- `backend/app/agents/graph/nodes/ceiling/ceiling_ack_entry_node.py`
- `backend/app/agents/prompts/ceiling_extraction_prompt.py`
- `backend/app/agents/schemas/ceiling_schema.py`
- The subcategory map from section 2.1, next to the schema, so payload and review read one table

### Modified files (AI backend)

- `backend/app/agents/schemas/supervisor_schema.py` — add the four intents
- `backend/app/agents/prompts/supervisor_prompt.py` — describe them, keep handover phrases on the handover intent, and send both ceiling names here
- `backend/app/agents/graph/nodes/supervisor_node.py` — include the intents in the action set the node already lists by hand
- `backend/app/agents/schemas/help_agent_schema.py` — permit `CREATE_CEILING_INSPECTION_SR` and `ACKNOWLEDGE_CEILING_INSPECTION_SR` for the tenant roles, imported from `ceiling_schema` (`CREATE_ROLES`, `ACK_ROLES`) so there is one list; permit review and approve for the matching roles
- `backend/app/agents/registry.py` — map both `OPEN_CEILING_INSPECTION` and `CLOSE_CEILING_INSPECTION` to `rdd_ceiling_inspection_agent`
- `backend/app/agents/registries/workflow_config.py` — register the config in section 7
- `backend/app/agents/graph/help_agent_graph.py` — add the nodes and set the entry for `rdd_ceiling_inspection_agent`
- Shared nodes that list agent names by hand: validation, missing field, merge, lease lookup, status sync, and the resource picker. Status sync must map both subcategories to this agent.
- Document upload allowlist: `FITOUT_INSPECTION_OTHER`, both draft report types, and both rejected-report types

### Modified files (frontend)

- `libs/ui-web/src/lib/components/serviceRequestChatbot/agenticSrNavigation.ts` — resolve the create target from the subcategory on the turn, so `rdd_ceiling_inspection_agent` opens `OPEN_CEILING_INSPECTION` or `CLOSE_CEILING_INSPECTION`
- The matching navigation spec
- Add `CREATE_CEILING_INSPECTION_SR` to the create stages, and the ceiling review stages to the non-create stages

### cenomi-api

No code change.

One data check before review is tested: the live workflow row shared by Open Ceiling and Close Ceiling must be level 1 `DD_ENGINEER` and level 2 `FM_MANAGER`. If it is still level 1 FM Manager and level 2 DD Engineer, or level 2 Mall Manager, correct that row in the environment. Do not change the agent to follow it.

---

## 10. Open Points for This Review

1. **Agent name.** This document uses `rdd_ceiling_inspection_agent`, matching `rdd_handover_agent` and `rdd_fitout_start_agent`. Confirm that name before build.
2. **Review payload shape.** `document_status_map` will be copied from one real form submission of each subcategory before the review update is implemented. The expected shape is the same.
3. **Live workflow row.** Confirm the environment under test has level 1 as DD Engineer and level 2 as FM Manager. The last flow migration in this repo writes the reverse order onto the shared row.
4. **Assign To targets.** Both workflow PDFs leave the target to the acting approver. This agent will not ship a fixed target-role list unless review asks for one.
5. **Tenant create permission on the platform.** In the frontend roles fixture only Authorized Signatory, Company Admin Central, Company Admin, and Service Provider hold `CAN_RAISE_HANDOVER_SR` among the tenant roles. If `POST /service-requests` checks that permission, the other tenant roles pass the chatbot gate but are rejected on submit. Confirm, and grant the permission to every tenant role in cenomi-api if so.

---

## 11. Out of Scope

- POI Architectural and POI MEP. They were on the original shared workflow row. These two PDFs declare the shared flow only between Open Ceiling and Close Ceiling, and POI has its own status branch in the API. Each becomes its own peer agent later.
- A second agent, graph, or intent set for Close Ceiling.
- A workflow Reject button.
- A fixed list of Assign To target roles.
- A chatbot rule that an earlier inspection must exist before either ceiling inspection can be raised.
- A new platform permission such as `CAN_RAISE_INSPECTION_SR`.
- Certificate of Insurance. That is a different product and is not part of this inspection family.
- Any change to cenomi-api application code.
- Correcting the Close Ceiling draft status in the frontend skeleton. The agent follows the API form.
