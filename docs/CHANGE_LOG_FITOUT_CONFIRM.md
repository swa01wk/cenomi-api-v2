# Change Log — AI chatbot + frontend integration

**Scope:** everything I've changed in the AI chatbot backend (this repo,
`cenomi-api-v2`) and the frontend (`cenomi-frontend-2`) to land the 3 Fitout
Confirmation service requests, the AI-chat contract fixes, and the Handover
Type prompt.

Platform (`cenomi-api-2`, the Go API) changes are tracked separately in that
repo at `docs/CHANGE_LOG_AI_LEASE_LOOKUP.md`.

**Format:** newest change on top. Each entry names the repo, the files, and
**why** the change was made. Update this doc every time we touch anything
on the chatbot or frontend side.

---

## 2026-08-17 · Correction loops, RDD document status, Path B comments, card/message wording

**Repos:** `cenomi-api-v2` (chatbot backend) + `cenomi-frontend-2` (tenant web)

**Why:** A day of live testing on staging surfaced nine defects. Seven were
reproduced locally (e2e or observability trace) before being fixed; two were
diagnosed from a captured network payload. Grouped below by the behaviour the
user saw, because several share a root cause.

### Mechanisms introduced on this branch

Two of these landed as staged work before the fixes below and had no changelog
entry of their own; the numbered sections that follow only ever described
repairs to them. Documenting them here so the design is on record.

#### A. Deterministic mid-flow field correction ("I want to change the title")

**The problem it solves.** While the bot is collecting field X, the user often
asks to revisit field Y instead. The LLM extractor cannot be trusted with this:
it is primed toward `active_field` and the previous assistant question, so it
binds the correction phrase to the field being collected rather than the one the
user named. Left to the LLM, "I want to change the title" while answering
`description` becomes a description of "I want to change the title".

**How it works.** A cheap regex detector runs *before* the extraction LLM call:

| Piece | Role |
|---|---|
| `services/field_correction_service.py` | `FIELD_SYNONYMS` (phrase → field), `_CORRECTION_CUE_RE` (request phrasings only), `detect_correction_intent()`, `is_unspecified_correction()` |
| `graph/state.py` | `correction_pending_field` — a **one-turn** signal, set by extraction and consumed+cleared by the very next `missing_field_node` run |
| `graph/nodes/shared/field_extraction_node.py` | Three arms: bare redirect → skip the LLM entirely; redirect **with** an inline value ("change the title to Foo") → point the extraction hint at the target field first; unspecified ("can you fix it") → block, so the phrase is never stored as a value |
| `graph/nodes/shared/missing_field_node.py` | Priority −1 branch builds `"Current {label}: {value}. What would you like to change it to?"` **verbatim**, with the field's own chips for enums, and refuses fields the merge layer would discard (backend-derived, or lease identifiers once `lease_id` is resolved) |
| `graph/help_agent_graph.py` | `_route_after_validation` sends a pending correction to `missing_field` even when the draft is otherwise complete |

**Why the re-ask is `verbatim`.** The prompt is built from `collected_data`,
which at that moment still holds the *old* value. An LLM rewrite reads that as
the field already being answered and re-confirms it instead of asking for a new
one.

**Deliberate non-goals.** The cue regex matches *request* phrasings only, never
the bare presence of a correction verb — otherwise a legitimate answer like
"Updated renovation title" would be read as a request to change the title and
re-ask forever. Sections 1 and 4 below are both cases where that line was drawn
in the wrong place.

#### B. Per-session turn serialisation

`services/chat_orchestration_service.py`

A turn is a read-modify-write over the whole draft: `load_session` reads
`collected_data`, the graph mutates it, `save_state` writes the entire blob
back. Two turns for one session running concurrently therefore lose one side's
work — last writer wins. Verified live: four parallel requests reduced a fully
populated draft (lease, dates, title, notes, comments) to a single field. Users
reach this by double-clicking send, a client retry, or two open tabs.

An `asyncio.Lock` per `session_id` serialises them, and the DB commit happens
**inside** the lock — the repositories only `flush()`, so a commit at request
teardown would land after the lock released and the next queued turn would read
pre-commit state. Holding the lock across the commit is what actually makes the
turns sequential rather than merely non-overlapping.

Scope: per-process, which fully covers a single uvicorn worker (how this service
runs today). Across workers or replicas this needs a Postgres advisory lock on
`session_id`; the `StateLoadError` guard in `ConversationStateService` is the
cross-process backstop that stops a failed read from destroying data.

#### C. Smaller conventions worth reusing

- **`verbatim` on `response_ui`** — a node that has authored exact user-facing
  text (concrete dates, the correction re-ask, the document-status question)
  sets this and `response_generation_node` returns it untouched. Reach for it
  whenever the wording carries a live value the LLM could restate wrongly.
- **A confirmation card owns its wording** — the confirmation nodes set
  `response_message = None` and put the text in the card. That text is now used
  as-is rather than fed to the LLM as a hint (section 8).
- **Direct-set chip actions bypass extraction** — chips whose handler writes
  `collected_data` directly (`_DIRECT_SET_CHIP_ACTIONS`,
  `_RDD_DIRECT_SET_CHIP_ACTIONS`) route straight to `merge_state`. Re-running
  extraction over a chip label finds nothing, and the correction re-arm then
  reads "field has a value + nothing extracted" as a failed correction and
  re-asks what the user just clicked.
- **Display flags must not double as contract flags** — `has_comments` drove
  both the card and the API payload, so hiding a row would have silently changed
  the request shape. Split into `payload_has_comments` (section 7).

### 1. Notes and comments re-asked forever

**Symptom:** the bot asked for notes, the user answered, and the same question
came straight back — for both notes and comments. Title, dates and inspector
were unaffected.

**Cause:** `is_unspecified_correction` classifies any message carrying a
correction verb but naming no field as an "I want to change something" request.
That path returns an empty extraction *by design*, so the answer was discarded
and the field re-asked. Notes and comments are the only fields users answer
with sentences, and handover prose routinely carries those verbs — "please
update the flooring before handover", "we need to change the shutters".
Retyping failed identically, so the flow could never move on.

- `graph/nodes/shared/field_extraction_node.py` — skip the unspecified-correction
  path while a free-text field (`title`, `description`, `notes`, `comments`) is
  being collected. No field is named in those messages, so there is nothing to
  redirect to; while one of these is active the message **is** the value.
- `services/field_correction_service.py::_find_matched_field` — match synonyms on
  whole words. The substring search read `end` out of "weekend", `start` out of
  "restart", `note` out of "denote" and `comment` out of "commentary", turning
  ordinary sentences into redirects to fields the user never mentioned.

### 2. Corrections silently swallowed in FM and RDD review

**Symptom:** "i want to edit the fitout start date" in RDD review answered with
"I see you want to change the fitout start date to *\<the current value\>*",
then every date typed afterwards was discarded and the question repeated.

**Cause:** every question saves `status = WAITING_FOR_USER`, so that value is
present on the *next* turn — the one carrying the reply. `_route_after_rdd_entry`
short-circuited on it as its **first** check, sending both correction requests
and answers to `response_generation`, which cannot write `collected_data`. The
LLM improvised from the values already there. Confirmed from the trace: the
turn never reached `field_extraction`.

- `graph/help_agent_graph.py` — extracted `_waiting_short_circuit_target` and
  applied it to the handover, FM and RDD entry routes. A stale WAITING status
  now still reaches `field_extraction` when the message is a correction request
  or an answer to the `active_field`; a genuine idle wait (the MM upload offer,
  which leaves `active_field` None) short-circuits as before. Only the CREATE_SR
  route had this guard; FM and RDD never did.
- `graph/help_agent_graph.py` — reordered `_route_after_rdd_entry` so
  `confirmation_status` / chip actions / `rdd_action` are checked **before** the
  stale status, matching the comment FM and handover already carried. This also
  fixed a second latent bug: a `CONFIRMED` set this turn was being shadowed by
  the previous turn's persisted status.

### 3. Path B corrections targeted fields Path B does not collect

**Symptom:** in Already Trading, "i want to edit the actual handover date"
answered "What would you like to **set** as the Actual Handover Date?" — *set*,
not *change*, because the field had no value: it is not part of Path B.

**Cause:** the synonym table is written for the standard handover path. Path B
records the same milestones under `actual_shop_handover_date`,
`actual_fitout_start_date` and `actual_fitout_end_date`, so three of its four
dates resolved to the wrong key and the edit went nowhere.

- `services/field_correction_service.py` — `_STAGE_SYNONYM_OVERRIDES`, layered
  over the base table for `CREATE_HANDED_OVER_SR` only. Every other stage
  resolves exactly as before.
- `graph/nodes/shared/field_extraction_node.py`, `graph/help_agent_graph.py` —
  thread `workflow_stage` into `detect_correction_intent` /
  `is_unspecified_correction`.

### 4. "guidline link" matched nothing

**Symptom:** "i want to edit guidline link" was answered with a request for the
actual handover date.

**Cause:** the misspelling (dropped `e`) and the bare plural `guidelines` were
both absent from the table, so the request fell through to the
unspecified-correction path and re-armed whichever field was active.

- `services/field_correction_service.py::_phrase_pattern` — plurals are now
  general rather than hand-listed: each synonym word matches its plural, so
  `guideline link` covers `guidelines link`, `guideline links` and
  `guidelines links`. Hand-listing both forms is how the `guidelines` plural
  went missing in the first place; this also fixes `notes`/`note`,
  `comments`/`comment` and `end dates` for free.
- Spelling variants added for `guideLineLink` only (`guidline`, `guide line`).

### 5. RDD PM was never asked the Meeting Report outcome

**Cause:** `get_missing_fields` gated `document_status` on `rdd_upload_doc_type`
— a field retired when uploads moved to the `document_requirement` card — so the
condition was dead. The chips, the entry-node handler, the card display and the
payload consumer all already existed and were simply unreachable.

- `schemas/handover_schema.py` — `document_status` is now a plain `RDD_REVIEW`
  required field, positioned between `guideLineLink` and the three dates. It
  needs no condition: before any upload `missing_field_node` returns the
  document card first, and after a *Rejected* upload `merge_state_node` has
  already written `DISAPPROVED`. What remains — a Meeting Report with no status
  picked — is exactly what the chips are for. Removed the dead conditional and
  the now-redundant `ALLOWED_EXTRACTED_FIELDS` entry.
- `graph/help_agent_graph.py` — `_RDD_DIRECT_SET_CHIP_ACTIONS` routes the two
  status chips straight to `merge_state`, so extraction's correction re-arm
  cannot re-ask the question the user just clicked.
- `graph/nodes/shared/missing_field_node.py` — the question text is built
  deterministically and marked `verbatim`. `field_questions` holds an
  instruction *about* asking ("Ask the RDD Project Manager for…"), which only
  becomes a real question if the LLM rewrite lands; when it did not, the chips
  rendered with nothing above them.
- `graph/nodes/shared/missing_field_node.py` — correction chips for
  `document_status`, `inspection_done_by` and `already_handovered` now carry the
  same `action` as their first-ask twins. Without it a correction click sent a
  bare label through LLM re-extraction, where the re-arm re-asks the question
  just answered.

### 6. Already Trading never asked for comments

**Cause:** `merge_state_node` filled the generated "No additional comments
provided for lease X." sentence while `comments` was still `None` — the exact
state `_route_after_validation`'s proactive-ask branch keys on — so the question
was consumed before it could be asked and every Path B SR shipped with the
placeholder.

- `graph/nodes/shared/merge_state_node.py` — dropped `CREATE_HANDED_OVER_SR`
  from the FM/RDD auto-satisfy block and moved its default to the CONFIRMED
  path, scoped per stage so Path B does not get invented `description`/`notes`
  it has no payload fields for.

### 7. Fitout confirmation cards showed a Comments row nobody fills

**Cause:** `_route_after_validation` excludes fitout agents from proactive
optional asking, so the row could only ever render blank.

- `schemas/fitout_confirm_schema.py` — `has_comments: False` for FOS and END
  (TRADING already was). Split out `payload_has_comments` so the display
  decision cannot silently alter the API contract — FOS/FOE still send
  `comments` unchanged.
- `graph/nodes/fitout_confirm/fitout_confirm_payload_builder_node.py` — reads the
  new key.
- `graph/nodes/handover/preview_node.py` — `_hidden_preview_keys` drops
  `comments` from the SR preview card for the three fitout sub-categories, on
  both the synced-state and live-GET branches.

### 8. Wording corrections

- `graph/nodes/shared/missing_field_node.py` — `_RADIO_PHRASING`. The three
  milestone fields read as nouns, so one shared label in "Has {label} started?"
  produced "Has fitout start started?" and "Has fitout complete started?" —
  the second doubly wrong, since completion is reached, not started. Each
  milestone now owns its question, subject, affirmed/negated verb and the two
  mid-sentence clauses.
- `graph/nodes/fitout_confirm/fitout_confirm_api_submission_node.py` — the create
  message no longer promises "It will be routed to the approver for review" on
  every submit. All three fitout confirmations are single-level workflows whose
  only approver is the role that raises them, so the platform approves them in
  the same transaction (Path B1 in the frontend spec). The message follows the
  **status the platform returned**; the caller's role is only a fallback when
  the response carries no status.
- `graph/nodes/shared/response_generation_node.py` — a confirmation card keeps
  its own wording. The confirmation nodes author their card text and set
  `response_message = None`; with no hint to rewrite, the LLM improvised from
  conversation history. Observed on an RDD card as "The fitout end date is
  currently set to 24/08/2026. Please confirm the new fitout end date you'd like
  to set." — the trace showed `merge_state` had already written 25/08 and the
  card below it showed the new value. Only the sentence was wrong, but it read
  as a loop.

### 9. Reopened Already Trading SR lost two dates

**Cause:** `payload_to_collected_data` never mapped the Path B keys the create
payload sends, so the preview card skipped them as empty.

- `services/review_decorations_service.py` — added `actual_shop_handover_date`,
  `actual_fitout_end_date`, `already_handovered` (the discriminator
  `HandoverAgenticBridge` reads to pick the HANDED_OVER form skeleton) and
  `notes`.

### Frontend (`cenomi-frontend-2`)

**Handover Meeting Report section missing until a manual reload.** Settled with
a captured network payload: the first fetch and the post-refresh fetch were
**byte-identical**, both containing the report attachment. The data was never
late — the frontend refused to look at it.

- `libs/ui-web/src/lib/components/serviceRequestForms/ServiceReqForms.tsx` — the
  `meetingReports` memo depended on `[!!sr_attachments, serviceCategory]`. The
  boolean only changes when the list flips between null and non-null, so
  replacing one array with another — exactly what a soft refresh does — left
  `meetingReports` frozen. On RDD review the page already held the MM/FM
  attachments, so the boolean never flipped when the Meeting Report arrived.
  Now depends on `sr_attachments` itself, plus `subServiceCategory`, which
  selects the document type and was missing from the list too.

  > `[!!something]` as a dependency will do this anywhere it appears — worth a
  > sweep of that file.

**Chips rendered with no question above them.**

- `libs/ui-web/src/lib/components/serviceRequestChatbot/useServiceRequestChat.ts`
  + new `silentTurnMessage.ts` — a *silent* turn (the debounced form-bridge
  patch) published its chips but returned before appending the assistant bubble.
  When the backend answered a background patch by asking the next question, the
  options rendered bare. The bubble is now shown when the turn actually asked
  something (a `text_question` naming a field) and that wording is not already
  the last thing on screen; genuine no-op patches stay silent.

### Tests

Backend — 7 new files, 4 updated:
`tests/e2e/test_notes_comments_prose_e2e.py`,
`tests/e2e/test_comments_and_rdd_status_e2e.py`,
`tests/e2e/test_handed_over_correction_e2e.py`,
`tests/unit/test_fitout_and_handed_over_cards.py`,
`tests/unit/test_review_stage_correction_routing.py`,
`tests/unit/test_response_generation_confirmation_card.py`,
`tests/unit/test_fitout_confirm_submission_message.py`;
plus additions to `test_field_extraction.py` and
`test_field_correction_service.py`, and updated expectations in
`test_handover_schema.py`, `test_rdd_doc_type_status.py`,
`test_rdd_workflow_order.py` and `test_missing_field_node.py` where they pinned
the retired `rdd_upload_doc_type` mechanism or the old chip shape.

Frontend — `silentTurnMessage.spec.ts` (8 tests).

Eval — S236–S244 (one per defect) plus S245–S246 for the correction
mechanism itself: the bare redirect arm and the unspecified-request guard.
S17 already covered a correction carrying an inline value, which resolves
through normal extraction. Grouped as `EVAL_2026_08_17`, runnable via
`run_eval.py --tier aug17`. See `tests/eval/scenarios.py`.

**Suite status:** backend 2035 passed / 58 failed — the same 58 that were
failing before this work began (RDD workflow ordering, validation service,
workflow config, several e2e lifecycles), and 2 of the original 60 were fixed
along the way. Frontend `ui-web` 219 passed / 8 failed, unchanged from baseline.
The pre-existing failures are unrelated to this session and remain open.

---

## 2026-08-07 · RDD final approve: remove user_action, fix success message to reflect tenant-acceptance step

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** After the RDDPM clicked Final Approve in the chatbot, two problems appeared in the platform:
1. The `operation_history` entry for DD_ENGINEER showed `status: "APPROVE"` (action verb) instead of `"APPROVED"` (state). This happened because the PATCH payload included `user_action: "APPROVE"`, which the Go platform logs verbatim as the history status.
2. The chatbot success message said "The service request is now complete" — incorrect. Reading the Go platform code (`operationsAfterApproveServiceRequest`, lines 10509-10558) confirmed that for `HANDOVER` sub_category the platform **always** sets SR status to `REPORT_SUBMITTED` after all admin operations (MM + FM + RDDPM) finish. `REPORT_SUBMITTED` is the intended intermediate state; the tenant must then accept or reject the handover report before the SR reaches `APPROVED`. The routing into `operationsRelatedToApproveServiceRequestOperation` is governed by `(userData.UserType == ADMIN && subCategory == HANDOVER)`, not by `user_action`, so the field was unnecessary.

**Fix:**

`services/payload_builder_service.py::build_rdd_approve_payload`
- Removed `user_action: "APPROVE"` from both the inner payload dict and the outer envelope. FM approval has never set this field (`user_action: None`), and Handover routing ignores it. Removing it causes the platform to record `"APPROVED"` (from the top-level `status: "APPROVED"`) in operation_history instead of the raw action string.

`graph/nodes/handover/rdd_api_submission_node.py`
- `backend_refs["rdd_status"]` changed from `"APPROVED"` to `"REPORT_SUBMITTED"` to accurately reflect the platform state after final approve.
- `response_message` updated: now explicitly states that all admin approvals are done, the SR is in Report Submitted status, and the tenant will be notified to accept or reject. Removes the misleading "service request is now complete" phrasing.

**Eval scenarios added:** S234, S235 in `tests/eval/scenarios.py`.

---

## 2026-08-07 · Fitout Confirmation APPROVE_SR: base dates, radio gate, session-wins merge, submit-loop fix

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** Several issues were found when testing the full FOS / FOE / Trading APPROVE_SR flow end-to-end:
1. Clicking "Confirm & Submit" on the fitout confirmation card looped back to the previous date question instead of submitting — the routing function tested `WAITING_FOR_USER` before `CONFIRMED`, short-circuiting to `response_generation` on the next turn even though the user had confirmed.
2. The form pre-fills radio values (`fitout_start_done`, `trading_done`, etc.) before the chat session starts. The old code treated a form-hydrated value as "user answered", skipping the required yes/no chat step entirely.
3. On APPROVE_SR, `sr_status_sync_node` re-hydrated the platform payload on every turn, overwriting any changes the user made in the chat session (e.g. changing `trading_done` from yes to no reset it back to yes on the next turn).
4. When asking the user to confirm an actual date, the agent showed a generic "what is the actual date?" prompt — the contract base date was never surfaced, so users had no reference and had to look it up manually.
5. The year-coercion guard (`can_coerce_year`) ran even when the user typed "yes" or "confirm", silently rewriting a backend-supplied date's year (e.g. `2025-10-31` → `2026-10-31`).

**Fixes:**

`graph/help_agent_graph.py::_route_after_fitout_confirm_entry`
- Check `confirmation_status == "CONFIRMED"` and `"REJECTED"` BEFORE `status == "WAITING_FOR_USER"`. The turn that renders the confirmation card ends in `response_generation`, which persists `WAITING_FOR_USER`. The old order short-circuited on `WAITING_FOR_USER` and re-emitted the previous question on the next turn, looping indefinitely.

`graph/nodes/fitout_confirm/fitout_confirm_entry_node.py`
- Action-override `confirm`/`confirm_approve_sr` now also clears stale `WAITING_FOR_USER` by returning `status: "IN_PROGRESS"`.

`graph/nodes/fitout_confirm/fitout_confirm_confirmation_node.py`
- APPROVE_SR branch emits `confirmAction: "confirm_approve_sr"` and `confirmLabel: "Approve"` so the frontend sends the right action and the router dispatches to merge_state.

`graph/nodes/fitout_confirm/fitout_confirm_payload_builder_node.py`
- `tenant_profile_id` now included in the APPROVE payload (was missing, caused platform 400s for some tenants).

`graph/nodes/shared/missing_field_node.py`
- Added `_ACTUAL_TO_ORIGINAL_DATE_CANDIDATES` map: for each actual-date field the agent asks for, it resolves the first non-empty base/planned date from `collected_data` and injects it into the question prompt (e.g. "The base date from the contract is 21/07/2026. Enter the actual date or confirm to keep it.").
- Added `_format_base_date_for_display()` — converts ISO/platform timestamps to DD/MM/YYYY.
- Radio gate: when `active_agent` is a fitout agent and `next_field` is `fitout_start_done`/`fitout_complete_done`/`trading_done` and `_approve_radio_confirmed` is not set, force the yes/no question to show even if the form pre-filled the value.
- `verbatim_question = True` flag set on original-date prompts so `response_generation_node` passes the message through without LLM rewriting (which was dropping the concrete date).

`graph/nodes/shared/response_generation_node.py`
- If `response_ui.verbatim == True`, bypass the LLM rewrite and return the message as-is.

`graph/nodes/shared/merge_state_node.py`
- `_DATE_TOKEN_RE = re.compile(r"\d")` — new guard: `can_coerce_year` now requires at least one digit in the user's message. "yes"/"confirm" answers no longer trigger year coercion that was silently rewriting backend-supplied years.
- `_FITOUT_RADIO_FIELDS` / `_FITOUT_RADIO_TO_ACTUAL` / `_FITOUT_RADIO_TO_PLANNED` — when the user answers the radio in the current session turn (detected via `active_field` + `extracted_keys`), set `_approve_radio_confirmed = True` and clear the opposite branch (actual date on yes→; planned dates + delay_reason on no→) so the agent re-collects the relevant dates.
- Added `_was_skipped()`, `_generate_description()`, `_generate_notes()`, `_generate_comments()` helpers — auto-fill deterministic defaults when the Mall Manager skips optional fields (answered "none"/"skip", value normalised to `""`). `None` (not yet asked) is left untouched.
- `_description_was_generated` flag — when the description was synthesised (not typed by the user), the title generator receives `""` instead of the generated sentence, avoiding a redundant slug.
- Confirm-time fallback: on `confirmation_status == "CONFIRMED"` in `CREATE_SR`, any still-`None` optionals (description, notes, comments) are promoted to `""` so the auto-generation block runs and the SR never reaches the platform with blank required fields.
- Path B fallback for `comments`: when `workflow_stage == "CREATE_HANDED_OVER_SR"` and `comments` is still `None`, generate a default rather than an empty string.
- APPROVE_SR merge order for fitout agents: `{**hydrated, **existing_collected}` — session answers always win over re-hydrated platform data.

`graph/nodes/shared/sr_status_sync_node.py`
- APPROVE_SR fitout: session `existing_collected` wins over `hydrated` (reversed merge order).
- Added welcome message for `APPROVE_SR` fitout SRs: "Opening Fitout Start/End/Trading Confirmation SR **{id}** for approval."

`schemas/fitout_confirm_schema.py`
- `TRADING` config: `delay_reason_required = True` (was False — delay reason is always required on the NO branch, same as FOS/END).
- `OPTIONAL_FIELDS`: removed `delay_reason` from the set (it is only optional when `radio == "yes"`).
- `get_missing_fields()` APPROVE_SR lease check: accept `lease_code` or `lease_id` as sufficient (open-SR hydration may not have `lease_brand_mall`).
- `_approve_radio_confirmed` added to the session-flag exclusion list — never asked for by `missing_field_node`.
- Radio-gate check: radio field always appears in missing list until `_approve_radio_confirmed` is set.

`services/payload_builder_service.py`
- `build_handover_payload` / `build_handed_over_payload`: safe title/description defaults when user skipped both (no more `KeyError` on missing `title`).
- `build_rdd_approve_payload`: `user_action: "APPROVE"` now set (was `None`). Without this, the platform updates only the operation row but skips the SR-level status promotion, leaving the SR in `REPORT_SUBMITTED` even after the RDD engineer clicks Approve.

`services/lease_lookup_service.py` / `graph/nodes/shared/lease_lookup_node.py`
- `LeaseLookupResult` gains `base_fitout_start_date`, `base_fitout_end_date`, `base_trading_date` fields with a Pydantic `model_validator` that normalises common platform aliases (e.g. `fitoutStartDate`, `plannedFitoutStartDate` → `base_fitout_start_date`).
- `lease_lookup_node` writes these three fields to `collected_data` so `missing_field_node` can surface them when asking for actual dates.

`registries/workflow_config.py`
- Date prompts for FOS/EON/TRADING changed from YYYY-MM-DD to DD/MM/YYYY (matches portal form display).
- Agent-specific `lease_code` prompts (FOS: "Fitout Start Confirmation", END: "Fitout End Confirmation", TRADING: "Trading Confirmation") instead of a shared generic string.
- Extraction prompt updated: "confirm shortcut" rule — when the assistant's previous question showed a base date and the user replies "confirm", extract the base date as the actual-date value.

`graph/nodes/handover/preview_node.py`
- `_PREVIEW_FIELD_ORDER` extended with fitout confirmation fields (`fitout_start_done`, `fitout_complete_done`, `trading_done`, `actual_fitout_complete_date`, planned dates, `delay_reason`) and base-date fields (`base_fitout_start_date`, `base_fitout_end_date`, `base_trading_date`) so the FE bridge can hydrate Formik from the draft preview.

**Eval scenarios added:** S228–S233 in `tests/eval/scenarios.py`.

---

## 2026-08-07 · MM Create handover: auto-generate description / notes / comments when skipped

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** The Mall Manager create flow (`CREATE_SR`, `CREATE_HANDED_OVER_SR`) asks for Description, Notes, and Comments as optional fields. When the user answered "none" or "skip", the field was normalised to `""` and submitted as a blank string. The platform HANDOVER form skeleton marks `comments` as required and shows `description` prominently on the SR card, so blank values left the SR looking incomplete.

Previously only `title` had an auto-generator; description/notes/comments had none.

**Fix:** `graph/nodes/shared/merge_state_node.py`
- `_generate_description(lease_code, brand, mall, already_trading)` — e.g. `"Handover inspection for lease t0105712 - Under Armour at Jawharat Jeddah."`
- `_generate_notes(lease_code)` — `"No additional notes provided for lease {lease_code}."`
- `_generate_comments(lease_code)` — `"No additional comments provided for lease {lease_code}."`
- `_was_skipped(value)` — returns `True` only when `value == ""` (skipped), not when `value is None` (not yet asked).
- Auto-generation runs for `workflow_stage in ("CREATE_SR", "CREATE_HANDED_OVER_SR")` after the fields are merged but before the title block.
- `_description_was_generated` flag prevents the synthesised description from being slugged into the title (would produce `"handover-t0112136-handover-inspection-for-lease-t0112136"`).
- On `confirmation_status == "CONFIRMED"`, any `None` optionals are promoted to `""` so generation runs even when a field was never asked.

---


## 2026-08-04 · Upload route accepts `SR_HANDED_OVER_OTHER` for MM (Path B)

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** `POST /api/v2/upload` returned 403/502 for the Path B optional
document because `_SHARED_OPTIONAL_DOC_TYPES` in
`app/api/routes/upload.py` only listed `SR_HANDOVER_OTHER`. Our new
`SR_HANDED_OVER_OTHER` type fell through `_upload_actions_for` into the
else branch (`["CREATE_HANDOVER_SR"]` only, no MM fallback list), which
in combination with the multi-action deny raised
`UPLOAD_SHARED_HANDOVER_DOCUMENT: requires CAN_RAISE_HANDOVER_SR or
CAN_FM_REVIEW_HANDOVER_SR or CAN_RDD_REVIEW_HANDOVER_SR`. When the
platform PUT /files subsequently failed on an unrecognised type the AI
route wrapped it as 502.

**Fix:** `app/api/routes/upload.py::_SHARED_OPTIONAL_DOC_TYPES` — added
`SR_HANDED_OVER_OTHER`. `_upload_actions_for` now returns the same
three-action OR-list it uses for `SR_HANDOVER_OTHER`, so MM's
`CAN_RAISE_HANDOVER_SR` permission grants access. `ALL_DOCUMENT_TYPES`
already contained `SR_HANDED_OVER_OTHER`, so the type-validation check
was fine.

**Not fixed here:** if the platform (`cenomi-api-2`) itself rejects
`SR_HANDED_OVER_OTHER` on `PUT /files`, the AI route will still bubble up
a 502. That's a platform-side allowlist question — flag if it recurs.

---

## 2026-08-04 · Path B pre-submission optional upload gate (SR_HANDED_OVER_OTHER)

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** The previous fix wrapped the CREATE_HANDED_OVER_SR confirmation
card in a `response_decorations.suggestions` + `document_requirement`
block, but `response_ui_finalize.finalize_response_ui` intentionally
refuses to overlay those decorations onto a `confirmation_card` (see the
guards at lines 22-23 and 29 of `response_ui_finalize.py`). So the
decorations were silently dropped — the user saw the review card with
Confirm & Submit / Cancel only, no way to attach documents. This also
did not match Path A's shape (Path A shows a document_requirement UI
on its own turn, not inline with the confirmation card).

**Fix:** Two-turn flow, mirroring Path A's post-submission MM upload
gate but running *before* submission (Path B is fast-path and terminates
on `HANDED_OVER_COMPLETED`, so post-submission uploads are impossible).

`app/agents/graph/nodes/handover/confirmation_node.py::confirmation_node`

- Before returning the CREATE_HANDED_OVER_SR confirmation card, check
  `collected_data.mm_optional_upload_status`. If it is not `done`, emit a
  `document_requirement` UI limited to a single
  `SR_HANDED_OVER_OTHER` row and set the status to `awaiting_upload`.
  Attach a Continue chip via
  `build_mm_optional_upload_continue_suggestions()`.
- `uploadContext` includes `allowedDocumentTypes:
  ["SR_HANDED_OVER_OTHER"]` and `defaultDocumentType:
  "SR_HANDED_OVER_OTHER"` — the FE `DocumentRequirementCard` already
  picks the sole listed doc type as the selected value when only one is
  offered, so no FE change is required to lock the dropdown.
- Removed the earlier (silently-dropped) decorations block from the
  confirmation-card return path.

`app/agents/graph/nodes/handover/handover_entry_node.py::_handle_mm_optional_upload_gate`

- Broadened the stage guard from `stage != "SR_CREATED"` to accept both
  `SR_CREATED` (Path A, post-submission) and `CREATE_HANDED_OVER_SR`
  (Path B, pre-submission). The Continue / skip / upload actions and the
  associated status transitions now fire for both paths.
- Skipped the `sr_id` early-return for Path B — Path B has no `sr_id`
  yet at this point (it is created on Confirm & Submit).

**Result:**

- After the user answers the four Path B dates + comments, chat shows a
  `document_requirement` card with a single "Other (Already Trading)"
  row and a Continue chip. The user can attach a file (writes to
  `collected_data.documents[]`; `build_create_handed_over_payload` reads
  it into `documents_ids`) or tap Continue.
- On Continue / "continue" / "skip" / "no", the MM gate marks the status
  DONE and the next turn returns the actual `confirmation_card`
  ("Handed Over Service Request" review).
- Nothing blocks Confirm & Submit — the upload remains optional
  throughout.

---

## 2026-08-04 · Upload-document chip on the Path B review card

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** The review card for CREATE_HANDED_OVER_SR only showed Confirm &
Submit / Cancel. The paperclip in the chat input works, but users don't
naturally see it as part of the "attach supporting docs" flow at the
confirmation step. The text nudge added earlier was insufficient.

**Fix:** `app/agents/graph/nodes/handover/confirmation_node.py::confirmation_node`
— on `CREATE_HANDED_OVER_SR`, attach a `response_decorations.suggestions`
chip labelled "Upload document" with `action: "upload_document"`. That
action is already handled by `handover_entry_node`. Any files the user
uploads flow through `collected_data.documents[]` into
`build_create_handed_over_payload` (which already reads `documents_ids`).

---

## 2026-08-04 · Regression fix — humanization must not touch state-sync payload

**Repo:** `cenomi-api-v2` (chatbot backend)

**Regression introduced by:** the earlier "Confirmation card: humanized
Handover Type + DD/MM/YYYY dates" change, which added `already_handovered`
to the enum-format branch of `format_field_display_value`.

**Symptom:** Selecting "Already Trading" from the AI chip stopped swapping
the form to HANDED_OVER. Backend flipped `workflow_stage` correctly but
the FE stayed on the HANDOVER skeleton.

**Diagnosis (reproduced via curl, not guess):**
`preview_node._fields_from_collected` calls `format_field_display_value`
per field to build `draft_preview.fields`. After the last change that
also humanized `already_handovered`, the payload now shipped
`already_handovered: "Already Trading"` instead of `"already_trading"`.

`useServiceRequestChat.mergeFromDraftPreview` writes exactly that value
into `draftFields`, `HandoverAgenticBridge.applyDraftFieldsToFormik` writes
it into Formik, and the `ServiceReqForms` observer compares
`values.already_handovered === 'already_trading'` (literal, case-sensitive).
`"Already Trading" !== "already_trading"` → observer skipped →
`onHandoverTypeChange` never fired → no swap.

**Fix:**

- `app/agents/services/display_formatters.py::format_field_display_value`
  — reverted the enum-format branch for `already_handovered`. Kept a
  comment explaining exactly why (state-sync uses raw codes).
- `app/agents/graph/nodes/handover/confirmation_node.py::_build_confirmation_card`
  — humanization moved here (only path where a human sees the value).
  Import `format_enum_label`; after the display_value assignment, when
  `field_key == "already_handovered" and not is_editable`, replace the
  value via `format_enum_label`. Review card still shows "Already
  Trading" / "Initiate Handover".

**Verified:** curl to `/chat/service-request` with
`action: select_handover_type_already_trading` on a fresh session returns
`draft_preview.fields.already_handovered == "already_trading"` again.

---

## 2026-08-05 · Title auto-gen only when user explicitly skips (Path A)

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** After making title an OPTIONAL asked field the bot STILL
wasn't asking — it jumped straight from description → start date.
Root cause: `merge_state_node` auto-generated the title as soon as
`description` had any value, treating `title=None` (untouched, not
yet asked) the same as `title=""` (user explicitly skipped). By the
time `missing_field_node` ran, title already had a fabricated value
and was skipped from the ask list.

**Fix:** `app/agents/graph/nodes/shared/merge_state_node.py` — Path A
title auto-gen now fires only when `title == ""` (empty string
explicitly set by the extractor for skip/none/n/a). `title is None`
is left alone so the missing-field loop can prompt. Path B keeps the
existing behaviour (fills on None too) because Path B never asks for
title. LLM extractor's existing declination handling maps
"skip"/"none"/… → `""` for any field, so the flow becomes:

1. missing_field asks title → user types one OR replies "skip"
2. If user typed one → merge leaves it alone, moves on to description
3. If user skipped → title="", merge waits for description
4. Once description arrives → merge fires the auto-gen
   ("handover-{lease}-{slug}") because title is `""`, not `None`

---

## 2026-08-05 · Title asked as optional + Path A pre-submission upload gate

**Repo:** `cenomi-api-v2` (chatbot backend)

**Two asks, one commit:**

1. Title was being auto-generated silently. User wants it asked as
   optional (like description) — bot prompts, user can accept a value
   or reply "skip"/"none" and the default is generated on submit.
2. Path A CREATE_SR stopped after `notes`/`comments` with no upload
   opportunity. User wanted the same optional supporting-doc upload
   Path B already has before the review card.

**Fix 1 — title:**

- `missing_field_node.py::_AUTO_GENERATED_FIELDS` → now empty (title
  removed).
- `handover_schema.py::OPTIONAL_FIELDS` → `title` added.
  `merge_state_node` still fabricates `handover-{lease}-{slug}` (Path A)
  or `Already Trading - {lease}` (Path B) when the resolved title is
  empty.
- `missing_field_service.py::HANDOVER_FIELD_QUESTIONS["title"]` —
  reworded to signal it's OPTIONAL and that "skip" / "none" is
  accepted.

**Fix 2 — Path A pre-submission upload gate:**

- `confirmation_node.py` — the upload gate condition broadened from
  `stage == "CREATE_HANDED_OVER_SR"` to
  `stage in ("CREATE_SR", "CREATE_HANDED_OVER_SR")`. Label / message
  branch on stage: Path B says "Other (Already Trading)", Path A says
  "Other". Same single-type SR_HANDOVER_OTHER + Continue chip contract.
- `handover_entry_node.py::_handle_mm_optional_upload_gate` — accepts
  all three stages: `CREATE_SR`, `CREATE_HANDED_OVER_SR`, `SR_CREATED`.
  So the Continue chip / skip / upload actions land here and mark
  `mm_optional_upload_status = done` regardless of which path emitted
  the gate.

Result: after `notes`/`comments`, the bot emits the document upload
card. User can attach a doc (writes to `collected_data.documents[]`
→ payload builder → `documents_ids`) or tap Continue. On Continue the
next turn shows the review card. Same UX Path B already ships.

---

## 2026-08-04 · Propagate date-format clears to the form (backend → FE bridge)

**Repos:** `cenomi-api-v2` (chatbot backend), `cenomi-frontend-2` (`feature/rdd-agents-integration`)

**Why:** The AI now rejects unparseable date input (my earlier
`validate_date_formats` fix), but the form preview kept showing the
invalid value (e.g. a lease code sitting in the Start Date field).
And when the user typed a valid replacement the field didn't update.

**Trace:**

- `validation_node` cleared the invalid value from `collected_data`,
  so on the same turn `draft_preview.fields` simply _omitted_ the
  field. FE's `mergeFromDraftPreview` skipped nulls, so `draftFields`
  kept the stale value. `HandoverAgenticBridge` had no diff to write.
  Formik never got told to clear.
- Both `buildFormikUpdatesFromDraft` and `applyDraftFieldsToFormik`
  had a `if (isEmpty(value)) continue` guard, so even if we did send
  an explicit `''` the bridge would drop it before writing.

**Fix — backend:**

- `app/agents/graph/nodes/shared/validation_node.py` — on `date_format`
  failures, remove the offending field from `collected_data` and
  return the mutated snapshot alongside `validation_errors` (already
  in place).
- `app/services/chat_orchestration_service.py` — compute a
  `cleared_fields: list[str]` from `date_format` errors and attach it
  to `draft_preview.cleared_fields`. Only emitted when non-empty.

**Fix — frontend (`feature/rdd-agents-integration`):**

- `libs/ui-web/src/lib/components/serviceRequestChatbot/useServiceRequestChat.ts::mergeFromDraftPreview`
  — read `draft_preview.cleared_fields`, set each listed key (and its
  `<key>LT` companion) to `''` in `draftFields`. Emitted before the
  normal field-merge loop so a valid new value from the same response
  can overwrite the clear.
- `libs/ui-web/src/lib/components/serviceRequestChatbot/handoverDraftFieldSync.ts`
  — added `CLEARABLE_DATE_FIELDS` / `CLEARABLE_DATE_KEYS` sets. Both
  `buildFormikUpdatesFromDraft` and `applyDraftFieldsToFormik` now
  allow `''` through for those keys (bypassing the "skip empty" guard
  and applying via `setFieldValue`) so the form input visibly clears.

Result: the second the AI rejects a bogus date entry, the
corresponding form field wipes. The user's next valid value then
updates the field via the normal draft-preview → bridge path.

---

## 2026-08-04 · Validate date-field format (per single field)

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** Agent accepted a lease code (`t0111595`) as a value for
`startDate` because the extractor routed the token into the field
slot and no per-field date-format check ran. `validate_start_end_date`
only fires when both dates are present, so a solo bogus value
sneaks through until the ordering check happens (or never).

**Fix — date format validation:**

- `app/agents/services/validation_service.py`:
  - New `_SINGLE_DATE_FIELDS` tuple listing every known date slot
    (startDate, endDate, unit_readiness_date, expected_handover_date,
    RDD chain, Path B actuals).
  - New `validate_date_formats(data)` — for each populated single
    date field, parse via `_parse_date`; on failure emit a blocking
    `date_format` result with `field=<field_name>` and message
    `"…value '<raw>' is not a valid date. Please enter it as
    DD/MM/YYYY (e.g. 25/07/2026)."`.
  - Wired into `validate_draft` step 2c, right before the pair-wise
    ordering checks. `missing_field_node` picks the blocking error
    and re-asks the exact field with its normal question hint.

Result: typing a lease code (or any non-date) into a date slot now
triggers an immediate re-ask on that specific field, and the "title"
question shows up in the create flow with a suggested value.

---

## 2026-08-04 · `inspection_done_by` chips (FM Manager / Operations)

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** The Path A CREATE_SR flow asked "Who will perform the
inspection: FM Manager or Operations?" as free text — same friction
we removed for the Handover Type prompt. Two fixed choices → chips.

**Fix:**

- `app/agents/graph/nodes/shared/missing_field_node.py` — added an
  `elif next_field == "inspection_done_by"` branch alongside the
  existing `already_handovered` / `document_status` chips. Emits
  `response_decorations.suggestions` with two `labeled_options` whose
  values (`FM_MANAGER`, `OPERATIONS`) match the enum codes the
  extractor and payload builder already accept — no schema change.
- `app/agents/graph/nodes/handover/handover_entry_node.py` — added
  `_INSPECTION_DONE_BY_ACTIONS` map so the chip's action string
  (`select_inspection_done_by_fm_manager` /
  `select_inspection_done_by_operations`) writes
  `collected_data.inspection_done_by` directly on click, mirroring the
  Handover Type chip handler.

Result: chat now shows two tap-to-select chips ("FM Manager" /
"Operations") for that turn. Typing still works — extraction path
untouched.

---

## 2026-08-04 · Eval suite — 8 new scenarios (S220–S227) for session changes

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** Everything landed this session needs a regression guard so
future rewrites don't silently break the Handover Type prompt, chip
handlers, Path B UX, and the humanization we just added.

**Fix:** `service-request-chatbot/backend/tests/eval/scenarios.py`

- Added 8 scenarios (S220–S227) covering:

| ID  | Tier | Guard |
|-----|------|-------|
| S220 | 1 | Handover Type prompt appears after lease resolution (Path A). |
| S221 | 1 | `select_handover_type_already_trading` chip flips stage → CREATE_HANDED_OVER_SR + sub_category HANDED_OVER + asks first actual date. |
| S222 | 2 | `select_handover_type_initiate_handover` chip stays on CREATE_SR (Path A). |
| S223 | 2 | Path B date-chain violation re-asks the LATER field (trading), not the earlier one (fitout end). |
| S224 | 1 | Path B review card renders Handover Type as "Already Trading" (humanized) + all 4 actual dates. |
| S225 | 2 | Path B proactively asks for `comments` once before the review card. |
| S226 | 2 | Pre-submission upload gate emits `document_requirement` with SR_HANDOVER_OTHER + Continue chip; Continue advances to review. |
| S227 | 2 | Submitted-SR preview shows Workflow Stages humanized (`Mall Manager: Completed`, not `MALL_MANAGER: FINISHED`). |

- Registered:
  - `TIER1_SCENARIOS` gets `S220, S221, S224` (core create-flow guards).
  - `TIER2_SCENARIOS` gets `S222, S223, S225, S226, S227` (edge / UX).
  - `HANDED_OVER_SCENARIOS` extended with all 8 for the selective
    runner.
- Docstring tier table updated to reflect the new IDs.

**Verified:** `SCENARIOS` total = 103 (was 95); all 8 IDs registered
with correct tier + enabled=True; parses cleanly under the backend
venv.

---

## 2026-08-04 · Humanize Workflow Stages row on the SR preview

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** The submitted-SR preview card was rendering raw platform enums:

```
Workflow Stages
MALL_MANAGER: FINISHED
```

**Fix:**

- `app/agents/services/display_formatters.py::ENUM_DISPLAY_LABELS` —
  added the SR workflow-operation statuses (`FINISHED → Completed`,
  `PENDING → Pending`, `IN_PROCESS/IN_PROGRESS → In Progress`,
  `SUBMITTED`, `REPORT_SUBMITTED`, `CANCELLED`, `SKIPPED`,
  `SR_CREATED`, `SR_APPROVED`, `SR_COMPLETED`,
  `HANDED_OVER_COMPLETED`).
- `app/agents/graph/nodes/handover/preview_node.py::_ops_field` —
  each `role` and `status` now runs through `format_enum_label` before
  the `role: status` line is assembled. Unknown codes fall through
  unchanged (safe default).

Result: `Mall Manager: Completed` instead of `MALL_MANAGER: FINISHED`.

---

## 2026-08-04 · Open-existing-SR on a terminal stage routes to `preview`

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** After creating a HANDED_OVER SR (terminal stage
`HANDED_OVER_COMPLETED`) the FE's `openExistingSr(srId)` call — which
posts `sr_id` on a fresh session with message `" "` (bubble text
"Open SR 320921") — returned FAQ content instead of the SR card:

> I don't have information about that yet.
> [Source: Mall Overview…]

**Trace:** `openExistingSr` posts `{ sr_id, message: '' }` on a
force-new session. `_route_after_load` sees the sr_id, routes to
`sr_status_sync`. The sync stamps `workflow_stage =
HANDED_OVER_COMPLETED`, then `_route_after_sync` hit the terminal-stage
branch and returned `"supervisor"`. The supervisor classified the bare
bubble text as `ASK_HELP` → FAQ node → RAG response with no SR context.

**Fix:** `app/agents/graph/help_agent_graph.py::_route_after_sync` —
when the workflow_stage is terminal, only fall through to `supervisor`
if `backend_refs.sr_id` is empty. When sr_id is present the user is
opening an existing SR and expects to see the preview card, so route
to `"preview"` instead. Non-open (stale terminal state without sr_id)
flow to supervisor unchanged.

---

## 2026-08-04 · Include `DELETE /documents` in the agentic Bearer allowlist

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** Chat card's Delete button called `DELETE /api/v2/upload` → AI
backend forwarded to `platform.delete_document` → `DELETE /documents` on
the platform. Response: `502 "Platform delete failed: HTTP 401"`.

Root cause: `platform_api_client._is_agentic_route` listed
`PUT /files`, `POST /service-requests`, `GET /leases`,
`GET/PATCH /service-requests/{id}` — but **not**
`DELETE /documents`. So `_uses_agentic_bearer` returned False,
`_auth_headers` sent the S2S access token (RSA), and the platform's
`ValidateAccessToken` rejected it because RSA can only verify cookie
JWTs, not S2S HMAC.

**Fix:** `app/agents/services/platform_api_client.py::_is_agentic_route`
— added `DELETE /documents` next to the existing `PUT /files` branch.
Now the AI-session Bearer is forwarded, `setContextFromAISessionClaims`
hydrates userData, and the platform's Delete usecase proceeds (Admin
users pass the tenant-profile check unconditionally).

---

## 2026-08-04 · Root-cause fix — use `SR_HANDOVER_OTHER` on Path B too

**Repos:** `cenomi-api-v2` (chatbot backend), `cenomi-frontend-2`

**Real root cause (found by reading the platform form skeleton):** the
platform's HANDED_OVER form skeleton seeds
`initialState.common.documents[0].document_type_id = "SR_HANDOVER_OTHER"`
(the Path A name). The dropdown option is the same. The AI was using a
newly-invented `SR_HANDED_OVER_OTHER`, which:

- Never matched the skeleton's starter row → `upsertDocumentRow`
  appended a second row instead of populating the starter.
- Wasn't in the form's document-type dropdown → the AI's row rendered
  with a fallback "Other" label and no file preview.

Result: the manual form showed the empty starter row plus an
AI-appended row that looked empty (no file preview) but had a Delete
button. Duplicate + broken.

**Fix:** dropped the invented `SR_HANDED_OVER_OTHER` type everywhere;
Path B now reuses `SR_HANDOVER_OTHER`:

- `app/agents/schemas/handover_schema.py` —
  `HandedOverDocumentType`, `HANDED_OVER_ALLOWED_DOCUMENTS` →
  `("SR_HANDOVER_OTHER",)`.
- `app/agents/graph/nodes/document_upload_node.py` —
  `_HANDED_OVER_DOCUMENT_TYPES` → `{"SR_HANDOVER_OTHER"}`.
- `app/agents/graph/nodes/handover/confirmation_node.py` — every
  `SR_HANDED_OVER_OTHER` reference in the upload gate flipped to
  `SR_HANDOVER_OTHER` (row key, allowedDocumentTypes,
  defaultDocumentType, uploaded_ids check).
- `app/api/routes/upload.py::_SHARED_OPTIONAL_DOC_TYPES` — trimmed back
  to just `SR_HANDOVER_OTHER` (removed the redundant new type).
- Reverted the earlier `upsertDocumentRow` starter-row hack in
  `handoverDraftFieldRegistry.ts` — no longer needed. `document_type_id`
  now matches the skeleton starter row naturally, so
  `findExistingDocumentRowIndex` returns the correct index and the row
  is populated in place.

**DB:** the earlier `SR_HANDED_OVER_OTHER` row inserted into
`cenomi.document_types` is now dead but harmless — safe to delete
later.

---

## 2026-08-04 · Fix duplicate documents row + Delete alignment

**Repo:** `cenomi-frontend-2` (`feature/rdd-agents-integration`)

**Two issues:**

1. **Duplicate row in the manual form after an AI upload.** The classic
   HANDED_OVER / HANDOVER form skeleton initialises Formik `documents[]`
   with a blank starter row so the "Drag or browse" zone renders on
   mount. `HandoverAgenticBridge`'s `syncDocumentsFromDraft` →
   `upsertDocumentRow` matched rows by `document_type_id` only; the
   starter row had no `document_type_id`, so the AI-uploaded row got
   appended as a new entry, leaving the empty starter row visible below
   it.
2. **Delete button misaligned** in the chat card — it wrapped or
   overlapped the filename on narrow widths because the `<li>` had no
   explicit flex layout and the filename could grow unbounded.

**Fix:**

- `libs/ui-web/src/lib/components/serviceRequestChatbot/handoverDraftFieldRegistry.ts::upsertDocumentRow`
  — when no row matches the incoming `documentTypeId`, look for the
  first row with an empty `document_type_id` (the skeleton's starter
  row) and populate it in place. Only appends if no starter row exists.
- `libs/ui-web/src/lib/components/serviceRequestChatbot/DocumentRequirementCard.tsx`
  — the `<li>` for each doc row now uses explicit
  `display: flex; align-items: center; gap: 8px; flex-wrap: nowrap`.
  Filename `<span>` truncates with ellipsis so it never pushes the
  Delete button off-row; the button gets `flex: 0 0 auto;
  white-space: nowrap` so it stays right-aligned and single-line.

---

## 2026-08-04 · DocumentRequirementCard: post-upload row + collapse controls

**Repo:** `cenomi-frontend-2` (`feature/rdd-agents-integration`)

**Why:** After a successful upload the chat card never visually confirmed
success — no filename was shown, and the Attach/Save controls stayed
visible so the card looked identical to "not uploaded yet". Path A's
classic form does the right thing: the browse zone flips to
`✓ ACK939503270310726.pdf   Delete`.

**Three bugs found in one place:**

1. Row list iterated `documents.map(...)` (the **prop**) instead of
   `localDocs.map(...)`. After `handleSave` flipped `localDocs[i].status
   → 'uploaded'`, the row still rendered `missing` because the prop
   only refreshes on the next backend turn.
2. The row never displayed the uploaded filename — only the doc-type
   label ("Other (Already Trading)"). `handleSave` didn't preserve
   `pendingFile.name` into `localDocs`.
3. The Attach + Save block stayed rendered even when every row was
   `uploaded`. Should collapse once nothing is left to attach.

**Fix:** `libs/ui-web/src/lib/components/serviceRequestChatbot/DocumentRequirementCard.tsx`

- Row list now iterates `localDocs`.
- `handleSave` stores `pendingFile.name` on the local doc row
  (`fileName` + `file_name` for compatibility with the draft schema).
- Uploaded rows render `✓ <filename> (Other (Already Trading))` +
  `Delete` inline, matching the manual form.
- The Attach/Comment/Save block is wrapped in `missingDocs.length > 0`
  so it disappears once every doc is uploaded; only the Continue chip
  (from `response_decorations.suggestions`) remains.

---

## 2026-08-04 · Allow silent refresh (empty message with session_id)

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** After a successful document upload the FE calls
`refreshAfterUpload()` → `sendTurn({ message: ' ', showUserBubble: false })`
which normalises to `message: ''` on the silent path (see
`useServiceRequestChat.ts:618`). The backend was rejecting this with
`422 "Either 'message' or 'sr_id' must be provided"` — so the doc-
requirement card never re-hydrated after the upload and reverted to
"Not uploaded" via the `useEffect([documents])` reset in
`DocumentRequirementCard`.

**Fix:** `app/api/routes/chat.py::chat_service_request` — relaxed the
guard to also accept when `session_id` is present. A silent refresh on
an existing conversation is a valid pattern (used by the doc upload
flow and any future "just re-emit current UI" refresh trigger). The
error message now lists all four acceptable fields:
`'message', 'sr_id', 'action', or 'session_id'`.

**Verified via curl:** POST `{session_id: "…", message: ""}` returns
200 (previously 422).

---

## 2026-08-04 · Drop `x-internal-api-token` from lease lookup — Bearer only

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** cenomi-api-2 now accepts the AI-session Bearer directly on the
canonical `GET /leases` route (PR 4472 cherry-pick applied on the
platform's dev branch). The dual-header workaround from earlier this
session is no longer needed and would keep routing lookups through the
service-token path — masking per-user audit on the platform.

**Fix:** `app/agents/services/lease_lookup_service.py::HttpLeaseLookupService`
— removed the `x-internal-api-token` header. Kept the comment block
explaining what changed so a future reader doesn't reintroduce it.

The `PLATFORM_INTERNAL_API_TOKEN` env var can stay set — it's used by
the S2S access-token bootstrap in `platform_api_client.py` (unrelated
to agentic-route calls).

---

## 2026-08-04 · Path B: proactively ask for comments + surface doc upload

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** The HANDED_OVER (Already Trading) flow jumped straight to the
review card once the 4 actual dates were collected. Comments (optional
field) was never asked, and the user had no signal that the paperclip
attachment widget worked for Path B too — even though
`build_create_handed_over_payload` already accepts `documents_ids` +
`attachments`.

**Fix:**

- `app/agents/graph/help_agent_graph.py::_route_after_validation` —
  extended the proactive-optional-ask branch (previously CREATE_SR only)
  to include `CREATE_HANDED_OVER_SR`, so `comments` is asked once before
  the confirmation card, matching Path A behaviour. Fitout agents are
  still excluded.
- `app/agents/graph/nodes/handover/confirmation_node.py` — appended a
  one-line prompt to the `CREATE_HANDED_OVER_SR` review-card message
  telling the user they can attach optional supporting documents via the
  paperclip icon before confirming. Any files they upload flow through
  the existing `documents` collection into the payload.

**Not scoped here:** a full pre-submission upload gate (chip-driven
"upload / skip" state machine like the SR_CREATED MM optional upload for
Path A). The paperclip already works, so a text nudge covers the user's
"give the option" ask without another round-trip. If we later want a
formal offer/skip prompt for Path B, add a
`MM_OPTIONAL_UPLOAD_STATUS_KEY`-style gate on `CREATE_HANDED_OVER_SR` in
`handover_entry_node`.

---

## 2026-08-04 · Confirmation card: humanized Handover Type + DD/MM/YYYY dates

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** The Path B review card was showing raw internal codes:
`already_trading` instead of "Already Trading", and `2026-07-20` instead
of `20/07/2026`. Two independent causes:

1. `display_formatters.format_field_display_value` had no branch for
   `already_handovered`, and `ENUM_DISPLAY_LABELS` didn't contain
   `already_trading` / `initiate_handover`.
2. `confirmation_node._build_confirmation_card` intentionally keeps raw
   ISO for **editable** fields (so the FE can round-trip them through
   `<input>` controls). My previous change had marked the four actual
   dates as editable, so they bypassed the DD/MM/YYYY formatter.

**Fix:**

- `app/agents/services/display_formatters.py` — added
  `already_trading`/`initiate_handover` (both cases) to
  `ENUM_DISPLAY_LABELS`; added `already_handovered` to the enum-format
  branch in `format_field_display_value`.
- `app/agents/graph/nodes/handover/confirmation_node.py` — trimmed
  `_HANDED_OVER_EDITABLE` down to `{title, comments}`. The four actual
  dates are display-only on the review card (user re-enters via chat if
  they need to change one), which restores DD/MM/YYYY formatting.

---

## 2026-08-04 · Stop killing `handoverAgenticActive` on sub-category swap (real fix)

**Repo:** `cenomi-frontend-2` (`feature/rdd-agents-integration`)

**Diagnosis:** Console logs (`[AI-HOST] gate`) confirmed
`handoverAgenticActive` flipped `true → false` at the exact moment
`subServiceCategory` changed from HANDOVER to HANDED_OVER. As a result,
`showBridge` became `false` and `HandoverAgenticBridge` unmounted — every
subsequent chat date sync was dropped.

**Root cause:** `ServiceRequestAgenticHost` had one `useEffect` that
combined "push mode into registry" (needs to re-run on `subServiceCategory`
change) with a cleanup that reset `handoverAgenticActive: false` (should
only fire on true unmount). React ran the cleanup on every dep change, so
the HANDOVER → HANDED_OVER swap silently disabled AI mode.

**Fix:** `libs/ui-web/src/lib/components/serviceRequestChatbot/ServiceRequestAgenticHost.tsx`

- Split the single effect into two:
  1. **Registry-update effect** (deps include `subServiceCategory`, no
     cleanup) — pushes current mode/category into the registry, never
     clears `handoverAgenticActive`.
  2. **Unmount effect** (empty deps, cleanup only) — clears the registry
     exactly once when the component actually unmounts (page navigation).
- The `HandoverAgenticBridge` `isHandoverCreate` broadening and the
  `agenticSrAccess` gate broadening from earlier today are still required
  (they get the bridge past HANDED_OVER page/pair checks); this fix
  addresses the last remaining reason `showBridge` was going false.

**Cleanup:** Removed the `[AI-HOST]` / `[AI-BRIDGE]` diagnostic
console.logs that were used to trace the bug in
`ServiceRequestAgenticHost.tsx` and `HandoverAgenticBridge.tsx`.

---

## 2026-08-04 · CREATE_HANDED_OVER_SR confirmation card

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** The pre-submit confirmation card was still showing Path A fields
(Description / Inspection Start Date / Inspection End Date / Inspection
Done By / Notes) for Path B handovers, because `_STAGE_DISPLAY_CONFIG` in
`confirmation_node.py` had no entry for `CREATE_HANDED_OVER_SR` and fell
through to the CREATE_SR defaults.

**Fix:** `app/agents/graph/nodes/handover/confirmation_node.py`

- Added Path B labels to `_FIELD_LABELS` (`already_handovered`,
  `actual_shop_handover_date`, `actual_fitout_start_date`,
  `actual_fitout_end_date`, `actual_trading_date`).
- Added `_HANDED_OVER_DISPLAY_FIELDS` (lease block + already_handovered +
  title + 4 actual dates + comments) and `_HANDED_OVER_EDITABLE`.
- Registered a `CREATE_HANDED_OVER_SR` entry in `_STAGE_DISPLAY_CONFIG` and
  a "Handed Over Service Request" label in `_REQUEST_TYPE_LABELS`.

---

## 2026-08-04 · Path B date-chain validation attaches error to the later field

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** For the HANDED_OVER Path B chain
(`actual_shop_handover_date ≤ actual_fitout_start_date ≤
actual_fitout_end_date ≤ actual_trading_date`),
`validate_handed_over_dates` was attaching every ordering error to the
**earlier** field. That produced this inverted UX: user just typed
`actual_trading_date = 03/07/2026`, backend detected the violation, but
`missing_field_node` re-asked for `actual_fitout_end_date` ("please enter
an end date on or before 03/07/2026") — i.e. it asked the user to change
an already-accepted date instead of the one they just typed.

**Fix:** `app/agents/services/validation_service.py::validate_handed_over_dates`
— pass `field=later_field` on the failing pair (instead of
`earlier_field`), and reworded the message so it reads "'actual_trading_date'
(03/07/2026) must be on or after 'actual_fitout_end_date' (30/07/2026)."
The missing-field flow now re-asks the last-typed field, which is what the
user expects.

---

## 2026-08-04 · Mount the AI bridge on the HANDED_OVER form (page-level gate)

**Repo:** `cenomi-frontend-2` (`feature/rdd-agents-integration`)

**Why:** Loosening `HandoverAgenticBridge.isHandoverCreate` alone was not
enough — the bridge component was never even rendered on the HANDED_OVER
page. Three page-level gates were still HANDOVER-only, so after the
skeleton swap the whole `<HandoverAgenticBridge/>` disappeared from the
tree and no chat draft ever reached Formik.

**Fix:** `libs/ui-web/src/lib/components/serviceRequestChatbot/agenticSrAccess.ts`
— broadened all three helpers to accept `HANDED_OVER` in addition to
`HANDOVER` for the FIT_OUT_AND_HANDOVER category:

- `isAiSupportedCreatePair` — feeds `isAiSupportedNewSrPage` and
  `canAccessAgenticCreateForPair`.
- `isHandoverNewSrPage` — used by `ServiceRequestAgenticHost` to decide
  `showBridge`. This was the actual gate that hid the bridge after the
  swap.
- `canAccessAgenticCreateForPair` — grants HANDED_OVER the same
  create-permission set as HANDOVER (SERVICE_REQUEST_CREATE or
  CAN_RAISE_HANDOVER_SR).

---

## 2026-08-04 · Enable `HandoverAgenticBridge` for the HANDED_OVER form

**Repo:** `cenomi-frontend-2` (`feature/rdd-agents-integration`)

**Why:** Backend now publishes `actual_shop_handover_date` (and the other
Path B date fields) in `draft_preview.fields`, and the FE registry has
`forceSync` bindings for them. But nothing was reaching Formik on the
HANDED_OVER form: chat responses arrive but the field never fills.

Root cause: `HandoverAgenticBridge`'s `isHandoverCreate` gate was checking
`subServiceCategory === HANDOVER` only. Once
`serviceReqWrapper.onHandoverTypeChange` swaps the form to `HANDED_OVER`,
that flag flips to `false`, `bridgeEnabled` becomes `false`, and the
useEffect that calls `applyDraftToFormik(draftFields)` no-ops. All
subsequent chat→form syncs are silently dropped.

**Fix:** `libs/ui-web/src/lib/components/serviceRequestChatbot/HandoverAgenticBridge.tsx`
— broadened `isHandoverCreate` to accept both `HANDOVER` and `HANDED_OVER`
sub-categories. Same file-name-agnostic diff logic works for both.

---

## 2026-08-04 · Publish Path B actual dates in `draft_preview.fields`

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** Same root cause as the `already_handovered` fix, but for the four
HANDED_OVER date fields (`actual_shop_handover_date`,
`actual_fitout_start_date`, `actual_fitout_end_date`,
`actual_trading_date`). The FE's `HandoverAgenticBridge` already had
registry bindings for these keys — but they never reached
`useServiceRequestChat.mergeFromDraftPreview` because `_PREVIEW_FIELD_ORDER`
in `preview_node.py` didn't list them.

**Fix:**

- `app/agents/graph/nodes/handover/preview_node.py` — added all four Path B
  date keys to both `_PREVIEW_FIELD_ORDER` (draft path) and
  `_PLATFORM_FIELD_ORDER` (submitted-SR path).
- `app/agents/services/display_formatters.py` — added the same four keys to
  `_DATE_FIELD_KEYS` so `format_field_display_value` emits DD/MM/YYYY
  instead of raw ISO on the preview card.

---

## 2026-08-04 · Expose `already_handovered` in `draft_preview.fields`

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** After the chip click set `collected_data.already_handovered =
"already_trading"` and flipped the backend stage to `CREATE_HANDED_OVER_SR`,
the frontend still stayed on the HANDOVER skeleton — the radio was not
pre-selected and the sub-category never switched. Root cause: the FE hook
(`useServiceRequestChat.mergeFromDraftPreview`) builds its `draftFields`
solely from `response.draft_preview.fields`, and `already_handovered` was
never included there. Without it, `HandoverAgenticBridge` had no value to
`setFieldValue` on Formik, so the ServiceReqForms observer that watches
`values.already_handovered` and calls `onHandoverTypeChange(...)` never
fired.

**Fix:** Added `("already_handovered", "Handover Type")` to
`_PREVIEW_FIELD_ORDER` in
`app/agents/graph/nodes/handover/preview_node.py`, right after the
lease/brand/mall block. `_fields_from_collected` (called by
`chat_orchestration_service` on every turn) now publishes the discriminator
whenever the user has picked a handover type. `format_field_display_value`
passes `already_handovered` through as-is, so the raw literal
(`already_trading` / `initiate_handover`) reaches the FE — matching the
values the FE's `isHandedOverSubCategorySelection` /
`isInitiateHandoverSelection` helpers recognise.

**End-to-end flow now:** chip click → `handover_entry_node` writes
`collected_data.already_handovered` (+ stage flip on already_trading) →
orchestrator emits `draft_preview.fields.already_handovered` →
`useServiceRequestChat` picks it up → `HandoverAgenticBridge.setFieldValue`
→ `ServiceReqForms` observer fires `onHandoverTypeChange` → `serviceReqWrapper`
swaps the skeleton to HANDED_OVER and pre-selects the radio.

---

## 2026-08-04 · Handover Type chip clicks — server-side action handler

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** Adding the `labeled_options` decoration alone was not enough. When
the FE sends the chip click as `action: "select_handover_type_*"` with a
blank `message`, the graph had no handler for those action ids. The
extractor saw an empty user message, missing_field re-fired the same
Handover Type prompt, and the state never advanced — the chat looped on the
same question.

**Fix:** Added a `_HANDOVER_TYPE_ACTIONS` map in
`app/agents/graph/nodes/handover/handover_entry_node.py` (alongside the
existing `confirm` / `cancel_sr` / `upload_document` action overrides):

- `select_handover_type_already_trading` → write
  `collected_data.already_handovered = "already_trading"` **and** flip
  `workflow_stage → CREATE_HANDED_OVER_SR`, `sub_category → HANDED_OVER`
  (matches the Path A→B switch that `merge_state_node.py` does for text
  answers).
- `select_handover_type_initiate_handover` → write
  `already_handovered = "initiate_handover"` and stay on Path A.

Same pattern as `rdd_review_entry_node.py`'s
`select_rdd_doc_*` / `select_rdd_status_*` handlers.

---

## 2026-08-04 · Handover Type chips (labeled options in the chat)

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** The Handover Type question expected the user to type "Already
Trading" / "Initiate Handover" as free text. Same pattern the RDD flow uses
for document-type / approval-status where we emit `labeled_options` chips.
Cheap to render as buttons and removes the typo failure mode.

**Files:**

- `app/agents/graph/nodes/shared/missing_field_node.py` — added an
  `elif next_field == "already_handovered"` branch that attaches
  `response_decorations.suggestions` with `kind: "labeled_options"` and two
  options:
  - `Already Trading` → value `already_trading`, action
    `select_handover_type_already_trading`
  - `Initiate Handover` → value `initiate_handover`, action
    `select_handover_type_initiate_handover`

  The values are the same literals the extractor already accepts
  (`handover_extraction_prompt.py`) and the Path A→B switch in
  `merge_state_node.py` reads. No new handler needed — the FE sends the
  chip's `value` back like any other user message.

---

## 2026-08-04 · Handover Type prompt after lease resolution

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** In the standard `create handover service request` flow, after the
lease resolved the bot jumped straight to the description question. The
portal form asks for **Handover Type\*** first (Already Trading vs Initiate
Handover), and the whole downstream field set changes based on that choice.
The chatbot had to match.

**Files:**

- `app/agents/schemas/handover_schema.py` — inserted `already_handovered`
  into `CREATE_SR_STAGE.required_fields`, right after the lease-resolution
  block and before `title`. Guarantees the missing-field loop asks this
  first.
- `app/agents/services/missing_field_service.py` — added the
  `already_handovered` entry to `HANDOVER_FIELD_QUESTIONS` with an explicit
  two-option prompt (Already Trading / Initiate Handover). Stops the LLM
  from free-forming.
- `app/agents/graph/nodes/shared/merge_state_node.py` — after merging, if
  `active_agent == "rdd_handover_agent"` and `workflow_stage == "CREATE_SR"`
  and `already_handovered == "already_trading"`, flip
  `workflow_stage → CREATE_HANDED_OVER_SR` + `sub_category → HANDED_OVER`.
  Existing Path B nodes then take over (4 actual dates → auto-approve).
- `tests/unit/test_handover_schema.py` — added the new field to
  `_EXPECTED_FIELDS` so completeness/count assertions still pass.
  **138/138 adjacent tests green.**

**Extractor:** no change needed — `handover_extraction_prompt.py` already
teaches the LLM to map "already trading" → `already_trading` and "initiate
handover" → `initiate_handover`.

---

## 2026-08-04 · Chatbot sends `x-internal-api-token` on lease lookup

**Repo:** `cenomi-api-v2` (chatbot backend)

**Why:** After pointing the chatbot's lease lookup at local `cenomi-api-2`,
requests returned **401 Unauthorized**. The chatbot only attached
`Authorization: Bearer <user_ai_jwt>` but the platform mounts `GET /leases`
in the `x-internal-api-token` group — the user JWT is ignored there.

**File:** `app/agents/services/lease_lookup_service.py`

- `HttpLeaseLookupService.lookup` now also attaches
  `x-internal-api-token: {settings.platform_internal_api_token}` alongside
  the Bearer. Matches the trust boundary documented in the platform port
  (see `cenomi-api-2/docs/CHANGE_LOG_AI_LEASE_LOOKUP.md`).

---

## 2026-08-04 · Env: real DB + real JWT secret + real internal token

**Repo:** `cenomi-api-v2` (chatbot backend, `.env`)

**Why:** Fresh clones ship with placeholder values. Each restart of local
dev loses the localhost/prod-token adjustments. Documented here so it's
obvious what to set.

- `DATABASE_URL` — `host.docker.internal` → `localhost:6004`.
  `host.docker.internal` only resolves inside Docker; uvicorn runs directly
  on the Mac.
- `JWT_SECRET_KEY` — placeholder → `8f2c4b19d6a8e7c1b3f5d9a2c6e1f4ab`. Must
  match cenomi-api-2's `AI_SESSION_TOKEN_SECRET` so the chatbot can verify
  AI session JWTs minted by the platform.
- `PLATFORM_INTERNAL_API_TOKEN` — updated to the real token from the local
  prod dump so service-to-service calls to cenomi-api-2 succeed.
- `SERVICE_REQUEST_API_BASE_URL` + `LEASE_TENANT_API_BASE_URL` — pointed at
  `http://127.0.0.1:8000/api/v1` so the chatbot talks to the local Go
  platform, not UAT.
- `LEASE_LOOKUP_USE_MOCK` — `true` → `false` so it actually hits the HTTP
  lookup instead of the hardcoded mock lease.

---

## 2026-08-04 · Frontend AI-chat contract fixes on `feature/rdd-agents-integration`

**Repo:** `cenomi-frontend-2`

**Why:** The `docs/fix-notes/agent_ui_contract_and_frontend_alignment.md`
audit flagged four FE gaps against the chatbot response contract. Handling
them so the UI reads the flags the agent already emits.

**Files:**

- `libs/api/src/lib/srChatbot/srChatbotApi.ts`
  - `SrChatbotChatAction` — added `cancel_sr`, `confirm_cancel_sr`,
    `decline_cancel_sr`, `cancel_update`, `upload_document`, all 5 RDD chip
    actions (`select_rdd_doc_*`, `select_rdd_status_*`), and fitout-confirm
    actions (`approve`, `update_and_approve`, `update`, `save_progress`, +
    the confirm/cancel_create_fitout_confirm variants).
  - `SrChatbotUiPayload` — typed `cancelled`, `stage_complete`,
    `workflow_complete`, `workflow_stage`, `action`, `cancelAction`.
  - `SrChatbotStatePayload` — added `workflow_stage_before` and
    `confirmation_status` (including `CANCEL_PENDING`).

- `libs/ui-web/src/lib/components/serviceRequestChatbot/useServiceRequestChat.ts`
  - New hook state: `cancelPending`, `cancelled`, `stageComplete`,
    `workflowComplete`.
  - New callbacks: `cancelSr()`, `confirmCancelSr()`, `declineCancelSr()`.
  - Derived `canCancelSr` — true when platform SR exists AND stage is
    SR_CREATED / FM_REVIEW / RDD_REVIEW.
  - `isComplete` now covers `SR_COMPLETED`, `HANDED_OVER_COMPLETED`,
    `CANCELLED` in addition to `SR_CREATED` / `SR_APPROVED`.
  - `createdSrId` now populates on every create-terminal stage (not just
    `SR_CREATED`) so fitout Path B1 auto-approve triggers success actions.
  - `PLATFORM_MUTATING_ACTIONS` — added `confirm_cancel_sr`,
    `update_and_approve`, `update`.

- `libs/ui-web/src/lib/components/AIChatWidget/{AIChatWidget,ChatPanel,SrFabMessages}.tsx`
  - Threaded the new cancel-lifecycle props end-to-end.
  - `SrFabMessages` renders: `cancelled` banner, `CANCEL_PENDING`
    Confirm/Decline pair, `canCancelSr` "Cancel this SR" button. Hides the
    ordinary `SuggestionFooter` while either flag is active. Also skips
    `HandoverSuccessActions` when the SR was cancelled.

- `libs/ui-web/src/lib/components/serviceRequestChatbot/handoverDraftFieldRegistry.ts`
  - Added draft-field bindings for Path B dates
    (`actual_shop_handover_date`, `actual_fitout_start_date`,
    `actual_fitout_end_date`, `actual_trading_date`, `already_handovered`)
    and for all fitout-confirmation fields (status radios, actual + planned
    dates, delay reason, base + prior-milestone readonly display fields).

**Tests:** 68/68 pass across 7 targeted suites
(`agenticSrAccess`, `fitoutConfirmProgress`, `HandoverAgenticBridge`,
`HandoverSuccessActions`, `serviceRequestAgenticRegistry`, `handoverDraft*`).

---

## Fitout Confirmation SRs — chatbot backend

**Repo:** `cenomi-api-v2` (chatbot, branch `dev`)

**Why:** Three new sub_categories under `FIT_OUT_AND_HANDOVER` need chatbot
agent support: `FIT_OUT_CONFIRM_FOS` (start), `FIT_OUT_CONFIRM_END` (end),
`FIT_OUT_CONFIRM_TRADING` (trading). Frontend enums + formConfig + workflow
rows already exist; the chatbot never routed them.

**New files** under `app/agents/`:

- `schemas/fitout_confirm_schema.py` — per-sub_category field map
  (`FITOUT_CONFIRM_FIELDS`), stage definitions, `PERMISSION_MAP`,
  `get_missing_fields`, `role_can_approve`, extraction Pydantic schema.
- `prompts/fitout_confirm_extraction_prompt.py` —
  sub_category-parameterised extraction prompt builder.
- `graph/nodes/fitout_confirm/*.py` — entry / confirmation /
  payload_builder / api_submission nodes. Api submission also implements
  Path B1 auto-approve (approver creates with "yes" → follow-up PATCH with
  `status=APPROVED`).
- `tests/unit/test_fitout_confirm_*.py` — 51 unit tests (schema, payload
  builder, permissions, workflow_config registration).

**Files edited:**

- `registries/workflow_config.py` — `_build_fitout_confirm_config()` +
  `_init_registry` entry.
- `registry.py` — 3 sub_category entries mapping to `fitout_confirm_agent`.
- `services/missing_field_service.py` — `FITOUT_CONFIRM_FIELD_QUESTIONS`.
- `services/permission_service.py` — action strings, per-role permissions,
  `ensure_can_create_fitout_confirm`, `ensure_can_approve_fitout_confirm`,
  `caller_can_approve_fitout_confirm`.
- `services/payload_builder_service.py` —
  `build_create_fitout_confirm_payload` +
  `build_fitout_confirm_approve_payload`. Empty-string fills for the unused
  Option A/B branch; `documents_ids` on FOS/END only; `comments` stripped.
- `schemas/help_agent_schema.py` — role → intent permissions for the 4 new
  intents (`CREATE_FITOUT_CONFIRM_{FOS,END,TRADING}_SR`,
  `APPROVE_FITOUT_CONFIRM_SR`).
- `schemas/supervisor_schema.py` — Literal expanded to include the 4 new
  intents + `CREATE_WORK_PERMIT_SR` (which was missing and would have
  failed too).
- `prompts/supervisor_prompt.py` — 4 new intent blocks + routing rules
  covering keyword classification and sub_category disambiguation.
- `graph/nodes/supervisor_node.py` — added 3 CREATE intents to
  `_CREATE_NEW_SR_INTENTS`.
- `graph/help_agent_graph.py` — imports, entry-node map, node registration,
  routing edges (`document_upload`, `registry`, `validation`,
  `fitout_confirm_entry`, `fitout_confirm_confirmation`), and
  `_route_after_validation` dispatch for `fitout_confirm_agent`.

**Design:** one parameterised `fitout_confirm_agent` handles all three
sub_categories, driven by `FITOUT_CONFIRM_FIELDS[sub_category]`. No
copy-paste per sub_category.

---

## Fitout Confirmation SRs — frontend integration

**Repo:** `cenomi-frontend-2` (branch `feature/rdd-agents-integration`)

**Why:** Match the backend agent so the FAB / agentic bridge picks up the
three new sub_categories and users can create them from the portal.

**Files:**

- `libs/ui-web/src/lib/components/serviceRequestChatbot/agenticSrAccess.ts`
  — `FITOUT_CONFIRM_SUBCATEGORIES` set, `isFitoutConfirmPair()`, extended
  `isAiSupportedCreatePair` and `canAccessAgenticCreateForPair` to accept
  the 3 sub_categories (reusing `CAN_RAISE_HANDOVER_SR` permission).
- `libs/ui-web/src/lib/components/serviceRequestChatbot/HandoverAgenticBridge.tsx`
  — `isFitoutConfirmCreate` branch widens `bridgeEnabled`; lease enrichment
  runs for fitout-confirm create pages as well.
- `libs/ui-web/src/lib/components/serviceRequestChatbot/AgenticSrTypePicker.tsx`
  — split the disabled "Fitout" tile into 3 enabled tiles for FOS / END /
  TRADING with sub_category-specific paths.
- `libs/ui-web/src/lib/components/serviceRequestChatbot/agenticSrNavigation.ts`
  — 3 new-SR path constants.
- `libs/ui-web/src/lib/components/serviceRequestChatbot/serviceRequestAgenticRegistry.ts`
  — `buildCreateSrSeedMessage` returns sub_category-specific seeds so the
  supervisor classifies correctly on first turn.
- `libs/ui-web/src/lib/components/serviceRequestChatbot/handoverAgenticFormConfig.ts`
  — `isFitoutConfirmDraft` + `computeFitoutConfirmProgress` so the progress
  meter uses the small fitout-confirm field set instead of the 17-field
  Handover one.
- `libs/ui-web/src/lib/components/serviceRequestChatbot/useServiceRequestChat.ts`
  — sub_category-aware progress meter.
- `libs/i18n/src/lib/locales/{enUS,ar}/translation.json` — 6 new tile
  strings.
- Spec docs under `libs/ui-web/src/lib/components/serviceRequestForms/`:
  `fitout-confirmation-service-requests.md`,
  `fitout-confirmation-ai-agent-integration.md`,
  `operations-confirm-date-validation-and-tracking-backend-recommendations.md`.
- Tests: `agenticSrAccess.spec.ts` (11), `fitoutConfirmProgress.spec.ts`
  (11), + `serviceRequestAgenticRegistry.spec.ts` seed-message cases (5).

---

## How to update this file

1. Any code, config, or env change on the chatbot backend or the frontend
   goes here. Platform (`cenomi-api-2`) changes go in that repo at
   `docs/CHANGE_LOG_AI_LEASE_LOOKUP.md`.
2. Add a new dated section on top. Older entries stay put.
3. Under each entry: **Repo**, **Why** (one sentence — the problem),
   **Files** (bullet per file with what changed). Attach test/build outcome
   if applicable.
4. Keep entries factual — no marketing language.
5. If a later change reverses something earlier, add a new dated entry that
   says so; don't rewrite history.
