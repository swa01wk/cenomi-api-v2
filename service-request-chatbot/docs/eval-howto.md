# Eval How-To — Handover Service Request Chatbot

Quick-start guide for running the evaluation suite against a live local backend.
No Docker orchestration needed for the two-terminal dev workflow.

---

## Tier Structure

| Tier | Runner | Scenarios | Scope | Typical runtime |
|------|--------|-----------|-------|-----------------|
| **0** | `run_eval.py` + `run_lifecycle_eval.py` | S201–S202 + L203 | Smoke — fast sanity gate | ~60 s |
| **1** | `run_eval.py` | S1–S15 | Core CREATE_SR happy path | ~5 min |
| **2** | `run_eval.py` | S16–S33, S60–S64 | Edge — corrections, multi-field, API, lease eligibility | ~8 min |
| **3** | `run_eval.py` | S34–S59 | Battle — issues 1–7 from `agent_ui_fixes.md` | ~12 min |
| **4** | `run_lifecycle_eval.py` | L134–L139 | FM\_REVIEW — date, save, approve, RBAC | ~10 min |
| **5** | `run_lifecycle_eval.py` | L140–L145 | RDD\_REVIEW — field order, dates, RBAC | ~12 min |
| **6** | `run_lifecycle_eval.py` | L146–L173 | Full lifecycle + state/payload battle + multi-file upload | ~25 min |
| **all** | both runners | all tiers | Equivalent to running 0→6 in sequence | ~75 min |

### Lease eligibility + multi-file (v1.4.1 / `doc_upload_fix`)

| ID | Tags | Notes |
|----|------|-------|
| S63 | `lease-eligibility`, `handover-exists` | Mock code `t0028568` → `ui.type=existing_handover` |
| S64 | `lease-eligibility`, `activation-pending` | Mock code `t0999999` → activation messaging |
| L173 | `multi-file`, `document-upload` | FM uploads 3 docs then approve |

Requires mock leases for S63/S64 (`LEASE_LOOKUP_USE_MOCK=true` or unset platform lease URL). See `docs/fix-notes/lease_eligibility_and_multifile_upload.md`.

---

## Two-Terminal Dev Workflow

### Terminal 1 — Backend

```bash
cd "/Users/swa/Desktop/cenomi 3/cenomi-api-v2/service-request-chatbot/backend"

# Start postgres + redis (first time, or after docker restart)
cd .. && docker compose up -d postgres redis && cd backend

# Apply migrations
alembic upgrade head

# Seed users (idempotent)
python scripts/seed_users.py

# Start the API server (keep this terminal open)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Wait for:
```
INFO:     Application startup complete.
```

### Terminal 2 — Eval runner

Run from the project root (`service-request-chatbot/`):

```bash
cd "/Users/swa/Desktop/cenomi 3/cenomi-api-v2/service-request-chatbot"

# --- Smoke gate (~60 s) ---
./run_agent_eval.sh --no-start --tier 0

# --- Core only (~5 min) ---
./run_agent_eval.sh --no-start --tier 1

# --- Core + Edge (~13 min) ---
./run_agent_eval.sh --no-start --tier 1,2

# --- Full CREATE_SR suite (~25 min) ---
./run_agent_eval.sh --no-start --tier 1,2,3

# --- FM review only (~10 min) ---
./run_agent_eval.sh --no-start --tier 4

# --- Full lifecycle (~47 min) ---
./run_agent_eval.sh --no-start --tier 4,5,6

# --- Everything (~75 min) ---
./run_agent_eval.sh --no-start --tier all

# --- Verbose turn-by-turn output ---
./run_agent_eval.sh --no-start --tier 1 --verbose

# --- Skip observability export ---
./run_agent_eval.sh --no-start --tier 1 --no-obs
```

Results land in `results/eval_YYYYMMDD_HHMMSS/` with:

```
REPORT.md               # aggregated pass/fail across all tiers
tier_0_smoke.json       # raw scenario results for tier 0
tier_1_core.json        # … etc
tier_0_smoke.log
tier_1_core.log         # full stdout for each tier
obs/                    # observability trajectory exports
metrics_baseline.json   # metrics snapshot before the run
metrics_final.json      # metrics snapshot after the run
```

---

## Running Individual Tiers Directly (without the shell wrapper)

Useful when iterating on a single scenario.

```bash
cd "/Users/swa/Desktop/cenomi 3/cenomi-api-v2/service-request-chatbot/backend"

# All of tier 1
python -m tests.eval.run_eval --tier 1

# A single scenario by ID
python -m tests.eval.run_eval --scenarios 5 --verbose

# Multiple specific scenarios
python -m tests.eval.run_eval --scenarios 5,12,18 --verbose

# Filter by tag
python -m tests.eval.run_eval --tags corrected_fields

# Tier 4 FM_REVIEW only (lifecycle)
python -m tests.eval.run_lifecycle_eval --tier 4

# FM phase only, injecting an existing SR UUID to skip CREATE_SR
python -m tests.eval.run_lifecycle_eval --tier 4 --phase fm --sr-id <UUID>

# Full lifecycle for one scenario with verbose
python -m tests.eval.run_lifecycle_eval --scenarios 134 --verbose
```

---

## Understanding Scenario IDs

| Range | Runner | Description |
|-------|--------|-------------|
| S1–S15 | `run_eval.py` | Core CREATE_SR flows (Tier 1) |
| S16–S33 | `run_eval.py` | Correction & API edge cases (Tier 2) |
| S34–S59 | `run_eval.py` | Agent fix battle tests (Tier 3) |
| S60–S62 | `run_eval.py` | Additional coverage (Tier 2) |
| S201–S202 | `run_eval.py` | Smoke scenarios (Tier 0) |
| L134–L139 | `run_lifecycle_eval.py` | FM\_REVIEW phase (Tier 4) |
| L140–L145 | `run_lifecycle_eval.py` | RDD\_REVIEW phase (Tier 5) |
| L146–L172 | `run_lifecycle_eval.py` | Full lifecycle (Tier 6) |
| L203 | `run_lifecycle_eval.py` | FM smoke scenario (Tier 0) |

Lifecycle IDs are in the 130–203 range to avoid collision with regular scenario IDs.

---

## Dynamic Dates

All dates in scenarios are computed at import time relative to `datetime.date.today()`.
Never hardcode `YYYY-MM-DD` in scenario files — use the helpers instead:

```python
from tests.eval.scenarios import _d, _START_A, _END_A

_d(30)   # → today + 30 days, ISO format
_START_A # → _d(30), the primary start anchor
_END_A   # → _d(32), the primary end anchor
```

Standard anchors (from `scenarios.py`):

| Anchor | Offset | Purpose |
|--------|--------|---------|
| `_START_A` | +30 | Primary start date (~1 month out) |
| `_END_A` | +32 | Primary end date |
| `_START_B` | +55 | Secondary start (~2 months) |
| `_END_B` | +57 | Secondary end |
| `_START_C` | +80 | Tertiary start (~3 months) |
| `_END_C` | +82 | Tertiary end |
| `_VIO_START` | +80 | Start for date-violation tests |
| `_VIO_END_BAD` | +75 | Invalid end (before `_VIO_START`) |
| `_VIO_END_OK` | +83 | Corrected end after violation |
| `_CORR_END_A` | +37 | Corrected endDate for group-A corrections |

---

## Field Collection Order

The agent collects fields in this order:

```
description → startDate → endDate → inspection_done_by → notes → comments → confirmation_card
```

**Notes field:** The agent explicitly asks "Any notes?" after `inspection_done_by`. Respond with
`"No notes."` or any valid note text. This is distinct from `comments`.

**Title:** Auto-generated — never prompt the user for a title. Do not add title turns to scenarios.

---

## Writing a New Scenario

```python
# In scenarios.py — minimal template
_S99 = Scenario(
    id=99,
    name="My new scenario",
    goal="Verify that X happens when Y",
    tier=2,                         # 0=smoke, 1=core, 2=edge, 3=battle
    tags=["my-tag"],
    turns=[
        Turn(
            message="I need to raise a handover SR for Nike at Riyadh Park",
            note="Greeting + SR intent",
            expect_ui_type="text_question",
        ),
        Turn(
            message=f"Renovation work. Start {_START_A}, end {_END_A}. Done by FM team. No notes. No comments.",
            note="Packed message covering description → comments",
            expect_ui_type="confirmation_card",
        ),
        Turn(
            message="confirm",
            action="confirm",
            note="Submit",
            expect_workflow_stage="SR_CREATED",
        ),
    ],
)

# Add to the appropriate tier list near the bottom of scenarios.py:
TIER2_SCENARIOS.append(_S99)
```

### Key Turn fields

| Field | Purpose |
|-------|---------|
| `message` | User text sent to the API |
| `action` | Optional API action (`confirm`, `cancel`, `approve_fm_review`, …) |
| `corrected_fields` | Dict of field overrides sent alongside the message |
| `selected_lease_id` | Lease ID to select when a `lease_selection` card is shown |
| `expect_ui_type` | Assert `response.ui.type` equals this value |
| `expect_workflow_stage` | Assert `response.state.workflow_stage` equals this |
| `expect_keywords` | At least one must appear in `response.message` (case-insensitive) |
| `expect_no_submit` | Assert `state.ready_to_submit == False` |

---

## Common Failure Patterns

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `expect_ui_type` mismatch at T2 | Extra/missing turn in conversation flow | Check if bot asked for a field you didn't mock yet |
| `SR_CREATED` not reached | Date in the past | Use `_d(N)` with N > 0 |
| `confirmation_card` appears one turn late | Packed message missing notes | Add "No notes." to the packed message |
| `corrected_fields` assertion fails | No context to correct | Add a full packed message + confirm turn before applying correction |
| Lifecycle `FM_REVIEW` not triggered | CREATE_SR phase failed | Run with `--verbose` and check SR creation logs |

---

## Typical CI Gate

```yaml
# .github/workflows/eval.yml (example)
- name: Smoke eval
  run: |
    ./run_agent_eval.sh --no-start --tier 0
  env:
    BACKEND_URL: http://localhost:8000
```

---

_Last updated: 2026-07-18_
