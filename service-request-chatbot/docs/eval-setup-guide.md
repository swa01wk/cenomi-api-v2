# Eval Suite — Setup & Scenario-by-Scenario Run Guide

**Codebase:** `cenomi-api-v2 / service-request-chatbot`  
**Last updated:** 2026-07-30

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | ≥ 3.11 |
| Docker Desktop | running |
| PostgreSQL + Redis | via Docker Compose |
| `uvicorn` | installed in venv |

---

## 0. Recommended Run Sequence

Use this as your checklist every time you run the eval suite end-to-end.

### Step 1 — One-time setup (first run only)

See [Section 1](#1-one-time-setup) for the full commands. The order matters:

1. Start Docker Desktop
2. Start Docker Compose (PostgreSQL + Redis)
3. Create `.venv` → `pip install -e ".[dev]"`
4. Copy `.env.example` → `.env`
5. `alembic upgrade head` (DB schema)
6. `python scripts/seed_users.py` (test users)

### Step 2 — Start the backend (Terminal 1, keep open)

```bash
cd "/Users/swa/Desktop/cenomi 3/cenomi-api-v2/service-request-chatbot/backend"
source ../.venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Wait for `Application startup complete.` before proceeding.

### Step 3 — Run tiers in order (Terminal 2)

All commands run from the `service-request-chatbot/` root.

The recommended order is **smoke first, then by feature area, happy → edge → battle:**

| Order | Tier | What it covers | Time |
|---|---|---|---|
| 1 | `0` | Per-agent sanity — all 6 agents | ~2 min |
| 2 | `0p` | Pipeline smoke — Handover A 3-phase + FOS lifecycle | ~5 min |
| — | — | **If smoke passes, continue below** | — |
| 3 | `1` | Handover A CREATE_SR — Happy paths | ~5 min |
| 4 | `2` | Handover A CREATE_SR — Edge cases | ~8 min |
| 5 | `3` | Handover A CREATE_SR — Battle / regressions | ~10 min |
| 6 | `3a` | Handover A FM_REVIEW — Happy | ~3 min |
| 7 | `3b` | Handover A FM_REVIEW — Edge | ~3 min |
| 8 | `3c` | Handover A FM_REVIEW — RBAC / Battle | ~2 min |
| 9 | `3d` | Handover A RDD_REVIEW — Happy | ~4 min |
| 10 | `3e` | Handover A RDD_REVIEW — Edge | ~5 min |
| 11 | `3f` | Handover A RDD_REVIEW — RBAC / Battle | ~2 min |
| 12 | `3g` | Full Lifecycle — Happy (CREATE → FM → RDD → COMPLETED) | ~3 min |
| 13 | `3h` | Full Lifecycle — Edge (corrections at each stage) | ~5 min |
| 14 | `3i` | Full Lifecycle — Battle (state isolation, persistence) | ~12 min |
| 15 | `4` | Handover B — Happy | ~3 min |
| 16 | `5` | Handover B — Edge + Battle + RBAC | ~5 min |
| 17 | `6a` | Fitout Start (FOS) — Happy | ~2 min |
| 18 | `6b` | Fitout Start (FOS) — Edge | ~3 min |
| 19 | `6c` | Fitout Start (FOS) — Battle + RBAC | ~3 min |
| 20 | `6d` | Fitout Start (FOS) — Lifecycle (CREATE → APPROVE) | ~2 min |
| 21 | `6e` | Fitout End (END) — Happy | ~2 min |
| 22 | `6f` | Fitout End (END) — Edge | ~3 min |
| 23 | `6g` | Fitout End (END) — Lifecycle (CREATE → APPROVE) | ~2 min |
| 24 | `6h` | Trading Confirmation — Happy | ~2 min |
| 25 | `6i` | Trading Confirmation — Edge | ~2 min |
| 26 | `6j` | Trading Confirmation — Battle + RBAC | ~2 min |
| 27 | `8` | FAQ + RBAC | ~5 min |

> **Why this order?** Smoke catches environment issues early. Within Handover A, CREATE_SR must pass before FM_REVIEW or RDD_REVIEW (they depend on a created SR). Full lifecycle runs last because it requires all 3 individual stages to be working. For the Fitout agents, happy → edge → battle → lifecycle within each agent makes failures easier to diagnose.

### Step 4 — Check results

Results land in `results/eval_<TIMESTAMP>/`. Start with `REPORT.md` for an aggregate pass/fail summary, then drill into `obs/` and `logs/` for any failures.

### Quick failure decision tree

| What failed | What to do |
|---|---|
| Smoke tier 0 / 0p | Fix the environment before running anything else |
| CREATE_SR tiers 1–3 | Do not run 3a–3i yet; a broken CREATE_SR cascades downstream |
| FM_REVIEW tiers 3a–3c | Run tier 1 with `--verbose` and confirm SR was created with a valid `sr_id` |
| RBAC tier (3c / 3f / 5 / 6c / 6j) not denied | Role check is missing in the agent prompt, not a test issue |
| "Keywords not found" warning | Soft warning only — scenario still passes if `workflow_stage` is correct |
| FOS/END tier 6a/6e flat fails but LC passes | Check `eval_user` field — FOS/END require DD_ENGINEER |
| TRADING tier 6h flat fails but LC passes | Check `eval_user` field — TRADING requires MALL_MANAGER |
| FOS/END lifecycle tier 6d/6g fails | Confirm a FOS/END SR was created first; `open_existing_sr` needs a valid SR ID |
| `Auth failed for aisha@cenomi.com` | Run `python scripts/seed_users.py` from `backend/` |
| `alembic.util.exc.CommandError` | Run `alembic upgrade head` from `backend/` |

---

## 1. One-time Setup

One-time environment setup is covered in the [project README quick-start](../../README.md#quick-start).

The steps are: start Docker infra → create `.venv` → `pip install -e ".[dev]"` → copy `.env.example` → `alembic upgrade head` → `python scripts/seed_users.py`.

Once the stack is running, return here and proceed from Section 2.

---

## 2. Start Services (Terminal 1 — keep open)

```bash
cd "/Users/swa/Desktop/cenomi 3/cenomi-api-v2/service-request-chatbot/backend"
source ../.venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Wait for:
```
INFO:     Application startup complete.
```

---

## 3. Tier Overview

| Tier | Runner | Scenario IDs | What it covers | Time |
|---|---|---|---|---|
| **0** | both | S200–S201, S203–S206 | **Smoke** — per-agent sanity (FAQ + Handover A/B + FOS/END/TRADING) | ~2 min |
| **0p** | lifecycle | L298–L299 | **Pipeline Smoke** — Handover A 3-phase + FOS create→approve | ~5 min |
| | | | **── Handover A — Stage 1: CREATE_SR (Mall Manager) ──** | |
| **1** | `run_eval` | S1–S15, S63–S64 | **CREATE_SR Happy** — core create flows | ~5 min |
| **2** | `run_eval` | S16–S33, S60–S68 | **CREATE_SR Edge** — corrections, date violations, payload fixes | ~8 min |
| **3** | `run_eval` | S34–S59 | **CREATE_SR Battle** — Issue 1–7 regressions | ~10 min |
| | | | **── Handover A — Stage 2: FM_REVIEW (FM Manager / Operations) ──** | |
| **3a** | lifecycle | L134–L135 | **FM_REVIEW Happy** — date+approve, save-progress | ~3 min |
| **3b** | lifecycle | L136–L137, L139 | **FM_REVIEW Edge** — NL date, multi-field, Operations role | ~3 min |
| **3c** | lifecycle | L138 | **FM_REVIEW Battle+RBAC** — DD_ENGINEER denied | ~2 min |
| | | | **── Handover A — Stage 3: RDD_REVIEW (DD Engineer) ──** | |
| **3d** | lifecycle | L140–L141 | **RDD_REVIEW Happy** — packed + turn-by-turn | ~4 min |
| **3e** | lifecycle | L142–L144 | **RDD_REVIEW Edge** — date-chain, APPROVED_WITH_CHANGES, NL dates | ~5 min |
| **3f** | lifecycle | L145 | **RDD_REVIEW Battle+RBAC** — FM_MANAGER denied | ~2 min |
| | | | **── Handover A — Full Lifecycle (all 3 phases) ──** | |
| **3g** | lifecycle | L146 | **Full LC Happy** — CREATE→FM→RDD→SR_COMPLETED clean run | ~3 min |
| **3h** | lifecycle | L147–L149 | **Full LC Edge** — corrections at each stage + MM upload gate | ~5 min |
| **3i** | lifecycle | L150–L172 | **Full LC Battle** — state isolation, persistence, payload contracts | ~12 min |
| | | | **── Handover B ──** | |
| **4** | lifecycle | L150–L151 | **Handover B Happy** — already_trading packed + gradual | ~3 min |
| **5** | lifecycle | L152–L159 | **Handover B Edge + Battle** — date ordering, path switch, RBAC | ~5 min |
| | | | **── Fitout Start (FOS) — DD_ENGINEER ──** | |
| **6a** | both | S73 + L170 | **FOS Happy** — yes-branch create → SR_CREATED | ~2 min |
| **6b** | both | S70, S75, S78, S82 + L171 | **FOS Edge** — no-branch/delay-required, cross-milestone date, title | ~3 min |
| **6c** | both | S79, S81 + L179, L181, L182 | **FOS Battle+RBAC** — payload contract, cancel, MALL_MANAGER denied, stale state | ~3 min |
| **6d** | lifecycle | L172 | **FOS Lifecycle** — APPROVE_SR: open existing FOS SR → SR_APPROVED | ~2 min |
| | | | **── Fitout End (END) — DD_ENGINEER ──** | |
| **6e** | both | S83 + L173 | **END Happy** — yes-branch create → SR_CREATED | ~2 min |
| **6f** | both | S71, S77 + L174 | **END Edge** — no-branch/planned-dates-required | ~3 min |
| **6g** | lifecycle | L175 | **END Lifecycle** — APPROVE_SR: open existing END SR → SR_APPROVED | ~2 min |
| | | | **── Trading Confirmation (TRADING) — MALL_MANAGER / OPERATIONS ──** | |
| **6h** | both | S74 + L176 | **TRADING Happy** — yes-branch create → SR_CREATED | ~2 min |
| **6i** | both | S72, S76 + L177 | **TRADING Edge** — no-branch, delay_reason optional | ~2 min |
| **6j** | both | S80 + L178, L180 | **TRADING Battle+RBAC** — approve, DD_ENGINEER denied | ~2 min |
| | | | **── FAQ + RBAC ──** | |
| **8** | `run_eval` | S201, S208–S212 | **FAQ + RBAC** — help queries, status check, RBAC denials | ~5 min |
| **all** | both | all tiers | Everything (per-agent sub-tiers) | ~83 min |

> **Runner key:** `run_eval` = `run_eval.py` (flat single-phase); `lifecycle` = `run_lifecycle_eval.py` (multi-phase); `both` = the shell script runs both.

---

## 4. Run Tiers (Terminal 2)

All commands run from the project root (`service-request-chatbot/`).

### Smoke tiers (fastest gate)

```bash
# Per-agent smoke — all 6 agents (~2 min)
./run_agent_eval.sh --no-start --tier 0

# Pipeline smoke — Handover A 3-phase + FOS create→approve (~5 min)
./run_agent_eval.sh --no-start --tier 0p

# Both smoke tiers together
./run_agent_eval.sh --no-start --tier 0,0p
```

### Handover A — Stage 1: CREATE_SR (Mall Manager)

```bash
# Happy path
./run_agent_eval.sh --no-start --tier 1

# Edge cases
./run_agent_eval.sh --no-start --tier 2

# Battle (Issues 1–7 regressions)
./run_agent_eval.sh --no-start --tier 3

# Full CREATE_SR suite
./run_agent_eval.sh --no-start --tier 1,2,3
```

### Handover A — Stage 2: FM_REVIEW (FM Manager / Operations)

```bash
# Happy path — date+approve, save-progress
./run_agent_eval.sh --no-start --tier 3a

# Edge — NL date, multi-field, Operations role
./run_agent_eval.sh --no-start --tier 3b

# Battle + RBAC — DD_ENGINEER denied
./run_agent_eval.sh --no-start --tier 3c

# Full FM_REVIEW suite
./run_agent_eval.sh --no-start --tier 3a,3b,3c
```

### Handover A — Stage 3: RDD_REVIEW (DD Engineer)

```bash
# Happy path — all-fields-packed + turn-by-turn
./run_agent_eval.sh --no-start --tier 3d

# Edge — date-chain validation, APPROVED_WITH_CHANGES, NL dates
./run_agent_eval.sh --no-start --tier 3e

# Battle + RBAC — FM_MANAGER denied
./run_agent_eval.sh --no-start --tier 3f

# Full RDD_REVIEW suite
./run_agent_eval.sh --no-start --tier 3d,3e,3f
```

### Handover A — Full Lifecycle (all 3 phases)

```bash
# Happy path — CREATE→FM→RDD→SR_COMPLETED clean run
./run_agent_eval.sh --no-start --tier 3g

# Edge — corrections at each stage + MM upload gate
./run_agent_eval.sh --no-start --tier 3h

# Battle — state isolation, persistence, payload contracts
./run_agent_eval.sh --no-start --tier 3i

# Full lifecycle suite
./run_agent_eval.sh --no-start --tier 3g,3h,3i
```

### Handover A — complete suite (all stages + all levels)

```bash
./run_agent_eval.sh --no-start --tier 1,2,3,3a,3b,3c,3d,3e,3f,3g,3h,3i
```

### Handover B (already-trading / HANDED_OVER)

```bash
# Happy path
./run_agent_eval.sh --no-start --tier 4

# Edge + battle (incl. RBAC)
./run_agent_eval.sh --no-start --tier 5

# Full Handover B
./run_agent_eval.sh --no-start --tier 4,5
```

### Fitout Start (FOS) — DD_ENGINEER

```bash
# Happy path — yes-branch create → SR_CREATED
./run_agent_eval.sh --no-start --tier 6a

# Edge — intent routing, no-branch/delay-required, cross-milestone date, title format
./run_agent_eval.sh --no-start --tier 6b

# Battle + RBAC — payload contract, cancel, MALL_MANAGER denied, stale state
./run_agent_eval.sh --no-start --tier 6c

# Lifecycle — APPROVE_SR: open existing FOS SR → SR_APPROVED
./run_agent_eval.sh --no-start --tier 6d

# Full FOS suite
./run_agent_eval.sh --no-start --tier 6a,6b,6c,6d
```

### Fitout End (END) — DD_ENGINEER

```bash
# Happy path — yes-branch create → SR_CREATED
./run_agent_eval.sh --no-start --tier 6e

# Edge — intent routing, no-branch/all-planned-dates-required
./run_agent_eval.sh --no-start --tier 6f

# Lifecycle — APPROVE_SR: open existing END SR → SR_APPROVED
./run_agent_eval.sh --no-start --tier 6g

# Full END suite
./run_agent_eval.sh --no-start --tier 6e,6f,6g
```

### Trading Confirmation (TRADING) — MALL_MANAGER / OPERATIONS

```bash
# Happy path — yes-branch create → SR_CREATED
./run_agent_eval.sh --no-start --tier 6h

# Edge — intent routing, no-branch, delay_reason optional
./run_agent_eval.sh --no-start --tier 6i

# Battle + RBAC — approve, DD_ENGINEER denied
./run_agent_eval.sh --no-start --tier 6j

# Full TRADING suite
./run_agent_eval.sh --no-start --tier 6h,6i,6j
```

### All Fitout per-agent tiers (FOS + END + TRADING)

```bash
./run_agent_eval.sh --no-start --tier 6a,6b,6c,6d,6e,6f,6g,6h,6i,6j
```

### FAQ + RBAC

```bash
./run_agent_eval.sh --no-start --tier 8
```

### All agents

```bash
./run_agent_eval.sh --no-start --tier all
```

---

## 5. Individual Scenario Commands

All `run_eval` commands: `cd backend && python -m tests.eval.run_eval`  
All `run_lifecycle_eval` commands: `cd backend && python -m tests.eval.run_lifecycle_eval`

### Smoke — per-agent

```bash
# All 6 smoke scenarios
python -m tests.eval.run_eval --scenarios 200,201,203,204,205,206

# Just Handover A smoke
python -m tests.eval.run_eval --scenarios 201

# Just FAQ smoke
python -m tests.eval.run_eval --scenarios 200
```

### Smoke — pipeline

```bash
# All pipeline smoke
python -m tests.eval.run_lifecycle_eval --scenarios 298,299

# Handover A 3-phase pipeline
python -m tests.eval.run_lifecycle_eval --scenarios 299

# FOS create→approve pipeline
python -m tests.eval.run_lifecycle_eval --scenarios 298
```

### Handover A — Stage 1: CREATE_SR

```bash
# Happy path (Tier 1)
python -m tests.eval.run_eval --scenarios 1,2,3,4,5

# Individual issue scenarios
python -m tests.eval.run_eval --scenarios 34,35,36  # cancel flow
python -m tests.eval.run_eval --scenarios 39,40,41  # time field
```

### Handover A — Stage 2: FM_REVIEW

```bash
# Happy path
python -m tests.eval.run_lifecycle_eval --tier 3a

# Edge cases (NL date, multi-field, Operations role)
python -m tests.eval.run_lifecycle_eval --tier 3b

# Battle + RBAC (DD_ENGINEER denied)
python -m tests.eval.run_lifecycle_eval --tier 3c

# Individual scenarios
python -m tests.eval.run_lifecycle_eval --scenarios 134,135  # happy
python -m tests.eval.run_lifecycle_eval --scenarios 136,137,139  # edge
python -m tests.eval.run_lifecycle_eval --scenarios 138  # RBAC

# Full FM_REVIEW suite
python -m tests.eval.run_lifecycle_eval --scenarios 134,135,136,137,138,139
```

### Handover A — Stage 3: RDD_REVIEW

```bash
# Happy path
python -m tests.eval.run_lifecycle_eval --tier 3d

# Edge cases (date-chain, APPROVED_WITH_CHANGES, NL dates)
python -m tests.eval.run_lifecycle_eval --tier 3e

# Battle + RBAC (FM_MANAGER denied)
python -m tests.eval.run_lifecycle_eval --tier 3f

# Individual scenarios
python -m tests.eval.run_lifecycle_eval --scenarios 140,141  # happy
python -m tests.eval.run_lifecycle_eval --scenarios 142,143,144  # edge
python -m tests.eval.run_lifecycle_eval --scenarios 145  # RBAC

# Full RDD_REVIEW suite
python -m tests.eval.run_lifecycle_eval --scenarios 140,141,142,143,144,145
```

### Handover A — Full Lifecycle

```bash
# Happy path (clean CREATE→FM→RDD→SR_COMPLETED)
python -m tests.eval.run_lifecycle_eval --tier 3g

# Edge (corrections at each stage + MM upload gate)
python -m tests.eval.run_lifecycle_eval --tier 3h

# Battle (state isolation, persistence, contracts)
python -m tests.eval.run_lifecycle_eval --tier 3i

# Individual scenarios
python -m tests.eval.run_lifecycle_eval --scenarios 146  # happy end-to-end
python -m tests.eval.run_lifecycle_eval --scenarios 147  # corrections at each stage
```

### Handover B (Path B)

```bash
# All Path B lifecycle
python -m tests.eval.run_lifecycle_eval --scenarios 150,151,152,153,154,155,156,157,158,159

# Happy paths only
python -m tests.eval.run_lifecycle_eval --scenarios 150,151

# RBAC battle
python -m tests.eval.run_lifecycle_eval --scenarios 159
```

### Fitout END happy path (new S83)

```bash
python -m tests.eval.run_eval --scenarios 83
```

### FAQ scenarios

```bash
python -m tests.eval.run_eval --scenarios 208,209,210,211,212
```

### Fitout lifecycle

```bash
# FOS yes-branch
python -m tests.eval.run_lifecycle_eval --scenarios 170

# END yes-branch
python -m tests.eval.run_lifecycle_eval --scenarios 173

# TRADING yes-branch
python -m tests.eval.run_lifecycle_eval --scenarios 176

# TRADING no-branch complete (now L177 → SR_CREATED)
python -m tests.eval.run_lifecycle_eval --scenarios 177
```

### Fitout Start (FOS) — individual scenarios

```bash
# Happy path (Tier 6a)
python -m tests.eval.run_eval --scenarios 73                        # yes-branch multi-turn
python -m tests.eval.run_lifecycle_eval --scenarios 170             # yes-branch single-turn

# Edge (Tier 6b)
python -m tests.eval.run_eval --scenarios 70                        # intent classification
python -m tests.eval.run_eval --scenarios 75                        # no-branch, delay required
python -m tests.eval.run_eval --scenarios 78                        # cross-milestone date ordering
python -m tests.eval.run_eval --scenarios 82                        # title auto-fill
python -m tests.eval.run_lifecycle_eval --scenarios 171             # no-branch with delay_reason

# Battle + RBAC (Tier 6c)
python -m tests.eval.run_eval --scenarios 79                        # DD_ENGINEER can create
python -m tests.eval.run_eval --scenarios 81                        # approve payload contract
python -m tests.eval.run_lifecycle_eval --scenarios 179             # cancel mid-flow
python -m tests.eval.run_lifecycle_eval --scenarios 181             # MALL_MANAGER denied FOS approve
python -m tests.eval.run_lifecycle_eval --scenarios 182             # stale state cleared after terminal

# Lifecycle (Tier 6d)
python -m tests.eval.run_lifecycle_eval --scenarios 172             # APPROVE_SR → SR_APPROVED

# Full FOS suite
./run_agent_eval.sh --no-start --tier 6a,6b,6c,6d
```

### Fitout End (END) — individual scenarios

```bash
# Happy path (Tier 6e)
python -m tests.eval.run_eval --scenarios 83                        # yes-branch multi-turn
python -m tests.eval.run_lifecycle_eval --scenarios 173             # yes-branch packed

# Edge (Tier 6f)
python -m tests.eval.run_eval --scenarios 71                        # intent classification
python -m tests.eval.run_eval --scenarios 77                        # no-branch, planned dates required
python -m tests.eval.run_lifecycle_eval --scenarios 174             # no-branch with delay_reason

# Lifecycle (Tier 6g)
python -m tests.eval.run_lifecycle_eval --scenarios 175             # APPROVE_SR → SR_APPROVED

# Full END suite
./run_agent_eval.sh --no-start --tier 6e,6f,6g
```

### Trading Confirmation (TRADING) — individual scenarios

```bash
# Happy path (Tier 6h)
python -m tests.eval.run_eval --scenarios 74                        # yes-branch multi-turn
python -m tests.eval.run_lifecycle_eval --scenarios 176             # yes-branch packed

# Edge (Tier 6i)
python -m tests.eval.run_eval --scenarios 72                        # intent classification
python -m tests.eval.run_eval --scenarios 76                        # no-branch, delay optional
python -m tests.eval.run_lifecycle_eval --scenarios 177             # no-branch without delay_reason → SR_CREATED

# Battle + RBAC (Tier 6j)
python -m tests.eval.run_eval --scenarios 80                        # MALL_MANAGER create permission
python -m tests.eval.run_lifecycle_eval --scenarios 178             # APPROVE_SR → SR_APPROVED
python -m tests.eval.run_lifecycle_eval --scenarios 180             # DD_ENGINEER denied TRADING approve

# Full TRADING suite
./run_agent_eval.sh --no-start --tier 6h,6i,6j
```

---

## 6. Scenario Reference

### Tier 0 — Per-Agent Smoke

| ID | Agent | Packed message | Assert |
|---|---|---|---|
| S200 | FAQ / Help | `"hello"` | greeting keywords |
| S201 | Handover A | All CREATE fields in 1 msg → confirm | `SR_CREATED` + `expect_field_value` on card |
| S203 | Handover B | All 4 dates packed → confirm | `HANDED_OVER_COMPLETED` |
| S204 | Fitout FOS | FOS yes + actual date → confirm | `SR_CREATED` |
| S205 | Fitout END | END yes + actual date → confirm | `SR_CREATED` |
| S206 | Fitout TRADING | TRADING yes + actual date → confirm | `SR_CREATED` |

### Tier 0p — Pipeline Smoke

| ID | Name | Phases | Assert |
|---|---|---|---|
| L298 | `fos_create_approve_pipeline_smoke` | CREATE_SR → APPROVE_SR | `SR_APPROVED` |
| L299 | `handover_a_full_pipeline_smoke` | CREATE_SR → FM_REVIEW approve | Phases complete in sequence |

### Tier 1 — CREATE_SR Happy

S1–S15, S63, S64 — Mall Manager CREATE_SR core happy paths.

### Tier 2 — CREATE_SR Edge

S16–S33, S60–S68 — corrections, multi-field, date violations, payload fields.

### Tier 3 — CREATE_SR Battle

S34–S59 — Issues 1–7 agent-fix regressions (cancel flow, time field, multi-lease, etc.).

### Tier 3a — FM_REVIEW Happy

| ID | Name | Assert |
|---|---|---|
| L134 | `FM Review — Happy Path (date + approve)` | `fm_status=APPROVED` |
| L135 | `FM Review — Save Progress then Approve` | `fm_status=APPROVED` after save then approve |

### Tier 3b — FM_REVIEW Edge

| ID | Name | Assert |
|---|---|---|
| L136 | `FM Review — Natural Language Date` | `unit_readiness_date` parsed to ISO |
| L137 | `FM Review — Date + Comments Together` | both fields extracted in one turn |
| L139 | `FM Review — Operations Role` | OPERATIONS role approves same as FM_MANAGER |

### Tier 3c — FM_REVIEW Battle + RBAC

| ID | Name | Assert |
|---|---|---|
| L138 | `FM Review — RBAC: DD_ENGINEER Cannot FM Approve` | denied: `["not authorised", "permission", "fm", "role"]` |

### Tier 3d — RDD_REVIEW Happy

| ID | Name | Assert |
|---|---|---|
| L140 | `RDD Review — Happy Path (all fields → submit → final approve)` | `SR_COMPLETED` |
| L141 | `RDD Review — Field Collection Turn by Turn` | correct field ordering; `SR_COMPLETED` |

### Tier 3e — RDD_REVIEW Edge

| ID | Name | Assert |
|---|---|---|
| L142 | `RDD Review — Date Chain Validation` | agent rejects inverted dates, accepts corrected |
| L143 | `RDD Review — Approve With Changes` | `document_status=APPROVED_WITH_CHANGES`; `SR_COMPLETED` |
| L144 | `RDD Review — Natural Language Dates` | all dates parsed from prose; `SR_COMPLETED` |

### Tier 3f — RDD_REVIEW Battle + RBAC

| ID | Name | Assert |
|---|---|---|
| L145 | `RDD Review — RBAC: FM_MANAGER Cannot RDD Approve` | denied: `["not authorised", "permission", "rdd", "role"]` |

### Tier 3g — Full Lifecycle Happy

| ID | Name | Phases | Assert |
|---|---|---|---|
| L146 | `Full Lifecycle — CREATE → FM_REVIEW → RDD_REVIEW → SR_COMPLETED` | 3 phases | `SR_COMPLETED` |

### Tier 3h — Full Lifecycle Edge

| ID | Name | Assert |
|---|---|---|
| L147 | `Full Lifecycle — With Corrections at Each Stage` | corrections accepted at all 3 phases; `SR_COMPLETED` |
| L148 | `CREATE_SR — MM Optional Upload Accepted` | MM offered upload, accepts, submits |
| L149 | `CREATE_SR — MM Optional Upload Declined` | MM declines upload, proceeds to confirm |

### Tier 3i — Full Lifecycle Battle

State contamination, session isolation, persistence, confirmation contracts (L150–L172 from TIER3 list):

| Group | IDs | What is tested |
|---|---|---|
| State contamination / session isolation | L150–L155 | stale `sr_id`, cancel cleanup, role isolation |
| rdd_action / fm_action persistence | L156–L158 | action flags survive across turns |
| Confirmation state lifecycle | L159–L162 | confirm/cancel state machine |
| Stale UI state | L163–L165 | UI type cleared after stage transition |
| active_field binding | L166–L168 | field binding across corrections |
| Payload contract | L169–L172 | expected fields present in every submitted payload |

### Tier 4 — Handover B Happy

| ID | Name | Turns | Assert |
|---|---|---|---|
| L150 | `handed_over_packed_2_turns` | 2 (packed) | `HANDED_OVER_COMPLETED` |
| L151 | `handed_over_gradual_multi_turn` | multi-turn | `HANDED_OVER_COMPLETED` |

### Tier 5 — Handover B Edge + Battle

| ID | Name | Assert |
|---|---|---|
| L152 | `date_chain_violation_then_fix` | agent rejects then accepts corrected dates |
| L153 | `title_auto_default` | title = `Already Trading - t0105712` |
| L154 | `title_explicit_override` | explicit title preserved |
| L155 | `optional_document_upload` | upload does not block submission |
| L156 | `mid_session_path_a_to_path_b` | mid-session switch handled |
| L157 | `mid_session_path_b_to_path_a` | switch back handled |
| L158 | `full_path_a_regression_after_path_b` | Path A unaffected |
| L159 | `path_b_rbac_non_mall_manager_denied` | DD_ENGINEER denied: `["not permitted", "mall manager"]` |

---

### Tier 6a — FOS Happy

| ID | Type | Name | Assert |
|---|---|---|---|
| S73 | flat | `fos_yes_branch_happy_path` | `SR_CREATED` — multi-turn: init → yes → actual date → confirm |
| L170 | LC | `fos_create_yes_branch_single_turn` | `SR_CREATED` + `expect_field_value` on `actual_fitout_start_date` |

### Tier 6b — FOS Edge

| ID | Type | Name | Assert |
|---|---|---|---|
| S70 | flat | `supervisor_classifies_confirm_fitout_start` | `CREATE_SR` + keywords: `["fitout", "start", "confirm", "yes", "no"]` |
| S75 | flat | `fos_no_branch_delay_reason_required` | agent asks for delay_reason after planned dates — keywords: `["delay", "reason", "why"]` |
| S78 | flat | `fos_cross_milestone_date_ordering` | FOS init + yes-branch confirmed; actual date prompt expected |
| S82 | flat | `fitout_confirm_title_auto_fill` | title auto-fills to `"Confirm Fitout Start - <pms_tenant_code>"` |
| L171 | LC | `fos_create_no_branch_with_delay` | `SR_CREATED` — all planned dates + `delay_reason` collected |

### Tier 6c — FOS Battle + RBAC

| ID | Type | Name | Assert |
|---|---|---|---|
| S79 | flat | `approve_fitout_confirm_sr_rbac_dd_engineer` | DD_ENGINEER can initiate FOS create flow — `CREATE_SR` |
| S81 | flat | `fos_approve_payload_has_status_approved` | approve path prompt accepted; agent enters APPROVE_SR context |
| L179 | LC | `fos_cancel_mid_flow` | cancel mid-flow clears state — `expect_no_active_agent=True` |
| L181 | LC | `rbac_mall_manager_cannot_approve_fos` | MALL_MANAGER denied FOS approve — keywords: `["permission", "not", "allowed"]` |
| L182 | LC | `fitout_confirm_stale_state_cleared_after_terminal` | second FOS request after SR_CREATED clears stale state — `CREATE_SR` on fresh start |

### Tier 6d — FOS Lifecycle (CREATE_SR → APPROVE_SR)

| ID | Name | Phases | Assert |
|---|---|---|---|
| L172 | `fos_approve_flow` | 1 (APPROVE_SR — opens existing FOS SR) | `SR_APPROVED` |

> **Requires an existing FOS SR** in `APPROVE_SR` state. The runner passes an `sr_id` captured from a prior CREATE_SR phase. Run tier 6a first if running 6d in isolation with `--scenarios 172`.

**FOS workflow lifecycle:**
```
CREATE_SR (DD_ENGINEER)          APPROVE_SR (DD_ENGINEER)
  ↓ radio: yes → actual_date       ↓ radio: yes → actual_date
  ↓ confirm → SR_CREATED           ↓ Update and Approve → SR_APPROVED
```

**FOS fields by branch:**

| Branch | Required fields |
|---|---|
| yes | `lease_brand_mall`, `fitout_start_done=yes`, `actual_fitout_start_date`, `comments` (optional) |
| no | `lease_brand_mall`, `fitout_start_done=no`, `planned_fitout_start_date`, `planned_fitout_end_date`, `planned_trading_date`, `delay_reason` (required), `comments` (optional) |

---

### Tier 6e — END Happy

| ID | Type | Name | Assert |
|---|---|---|---|
| S83 | flat | `end_yes_branch_happy_path` | `SR_CREATED` + `expect_field_value` on `actual_fitout_complete_date` |
| L173 | LC | `end_create_yes_branch` | `SR_CREATED` + `expect_field_value` on `actual_fitout_complete_date` |

### Tier 6f — END Edge

| ID | Type | Name | Assert |
|---|---|---|---|
| S71 | flat | `supervisor_classifies_confirm_fitout_end` | `CREATE_SR` + keywords: `["fitout", "complete", "end", "confirm", "yes", "no"]` |
| S77 | flat | `end_no_branch_planned_dates_required` | no-branch init; agent asks for planned dates — keywords: `["planned", "date", "complete"]` |
| L174 | LC | `end_create_no_branch` | `SR_CREATED` — planned dates + `delay_reason` collected |

### Tier 6g — END Lifecycle (CREATE_SR → APPROVE_SR)

| ID | Name | Phases | Assert |
|---|---|---|---|
| L175 | `end_approve_flow` | 1 (APPROVE_SR — opens existing END SR) | `SR_APPROVED` |

> **Requires an existing END SR** in `APPROVE_SR` state. Run tier 6e first if running 6g in isolation with `--scenarios 175`.

**END workflow lifecycle:**
```
CREATE_SR (DD_ENGINEER)          APPROVE_SR (DD_ENGINEER)
  ↓ radio: yes → actual_date       ↓ radio: yes → actual_date
  ↓ confirm → SR_CREATED           ↓ Update and Approve → SR_APPROVED
```

**END fields by branch:**

| Branch | Required fields |
|---|---|
| yes | `lease_brand_mall`, `fitout_complete_done=yes`, `actual_fitout_complete_date`, `comments` (optional) |
| no | `lease_brand_mall`, `fitout_complete_done=no`, `planned_fitout_complete_date`, `planned_trading_date`, `delay_reason` (required), `comments` (optional) |

---

### Tier 6h — TRADING Happy

| ID | Type | Name | Assert |
|---|---|---|---|
| S74 | flat | `trading_yes_branch_happy_path` | `SR_CREATED` — multi-turn: init → yes → actual date → confirm |
| L176 | LC | `trading_create_yes_branch` | `SR_CREATED` + `expect_field_value` on `actual_trading_date` |

### Tier 6i — TRADING Edge

| ID | Type | Name | Assert |
|---|---|---|---|
| S72 | flat | `supervisor_classifies_confirm_trading` | `CREATE_SR` + keywords: `["trading", "confirm", "started", "yes", "no"]` |
| S76 | flat | `trading_no_branch_delay_reason_optional` | no-branch with planned date; delay_reason omitted — flow does NOT block |
| L177 | LC | `trading_create_no_branch_no_delay_reason` | `SR_CREATED` — planned date provided, delay_reason never asked (optional for TRADING) |

### Tier 6j — TRADING Battle + RBAC

| ID | Type | Name | Assert |
|---|---|---|---|
| S80 | flat | `approve_fitout_confirm_sr_rbac_mall_manager` | MALL_MANAGER can initiate TRADING create flow — `CREATE_SR` |
| L178 | LC | `trading_approve_flow` | `SR_APPROVED` — MALL_MANAGER approves existing TRADING SR |
| L180 | LC | `rbac_dd_engineer_cannot_approve_trading` | DD_ENGINEER denied TRADING approve — keywords: `["permission", "not", "allowed", "approve", "trading"]` |

> **Requires an existing TRADING SR** for L178. Run tier 6h first if running 6j in isolation with `--scenarios 178`.

**TRADING workflow lifecycle:**
```
CREATE_SR (MALL_MANAGER)         APPROVE_SR (MALL_MANAGER / OPERATIONS)
  ↓ radio: yes → actual_date       ↓ radio: yes → actual_date
  ↓ confirm → SR_CREATED           ↓ Update and Approve → SR_APPROVED
```

**TRADING fields by branch:**

| Branch | Required fields |
|---|---|
| yes | `lease_brand_mall`, `trading_done=yes`, `actual_trading_date` |
| no | `lease_brand_mall`, `trading_done=no`, `planned_trading_date`, `delay_reason` (optional — does not block) |

> TRADING does **not** have documents or comments sections unlike FOS and END.

---

### RBAC enforcement summary (Fitout agents)

| Tier | Who tries | What they try | Expected result |
|---|---|---|---|
| 6c | `aisha` (MALL_MANAGER) | Approve FOS SR (APPROVE_FITOUT_CONFIRM_SR for FOS) | Denied — `["permission", "not", "allowed"]` |
| 6j | `sara` (DD_ENGINEER) | Approve TRADING SR (APPROVE_FITOUT_CONFIRM_SR for TRADING) | Denied — `["permission", "not", "allowed", "approve", "trading"]` |

### Tier 8 — FAQ + RBAC

| ID | Name | Message | Assert |
|---|---|---|---|
| S201 | `Smoke — Handover A` | Handover A packed | `SR_CREATED` |
| S208 | `faq_process_question_documents` | `"what documents do I need for handover checklist?"` | `["document", "checklist", "upload"]` |
| S209 | `faq_sr_status_check` | `"what is the status of my service request"` | `["status", "SR", "reference"]` |
| S210 | `faq_unknown_intent_graceful_fallback` | `"banana banana banana"` | any response, no crash |
| S211 | `rbac_dd_engineer_handover_denied` | `"I want to create a handover service request"` | `["not permitted", "not allowed"]` |
| S212 | `faq_mixed_intent_routes_to_create` | `"hello, I also need a handover for t0105712"` | `workflow_stage=CREATE_SR` |

---

## 7. Verbose Output

Add `--verbose` to any command to see turn-by-turn bot messages:

```bash
./run_agent_eval.sh --no-start --tier 0 --verbose
python -m tests.eval.run_eval --scenarios 1 --verbose
python -m tests.eval.run_lifecycle_eval --scenarios 150 --verbose
```

---

## 8. Results

Results land in `results/eval_<TIMESTAMP>/`:

| File | Contents |
|---|---|
| `REPORT.md` | Aggregate pass/fail table across all executed tiers |
| `tier_*.log` | Raw console output per tier |
| `tier_*.json` | Structured pass/fail data per tier |
| `obs/` | Observability traces (session-level markdown) |
| `logs/` | Per-scenario log snippets for failures |

---

## 9. Common Failures

| Symptom | Likely cause | Fix |
|---|---|---|
| `S201 confirmation_card not returned` | Packed message fields not extracted | Run `--verbose`, check Turn 1 response for field extraction |
| `S203 HANDED_OVER_COMPLETED not reached` | Date chain violation in packed message | Ensure `handover ≤ fitout_start ≤ fitout_end ≤ trading`, all in the past |
| `S204/S205/S206 workflow_stage wrong` | Supervisor routes to wrong agent | Check `supervisor_prompt.py` keyword lists for FOS/END/TRADING |
| `S208–S212 not found` | Tier 8 not imported in runner | Run `python -m tests.eval.run_eval --scenarios 208,209,210,211,212` directly |
| `Tier 0p pipeline fails at FM_REVIEW entry` | Phase 1 CREATE_SR failed to return SR ID | Run with `--verbose`, check Phase 1 logs for SR ID capture |
| `Tier 3c/3f RBAC test not denied` | Role check missing in agent prompt | Verify role is enforced in FM/RDD agent prompts |
| `Tier 6a/6e flat fails — workflow_stage wrong` | FOS/END eval_user not DD_ENGINEER | Scenario `eval_user` must be `sara@cenomi.com` (DD_ENGINEER) |
| `Tier 6h flat fails — workflow_stage wrong` | TRADING eval_user not MALL_MANAGER | Scenario `eval_user` must be `aisha@cenomi.com` (MALL_MANAGER) |
| `Tier 6c L181 RBAC test not denied` | FOS approve role check missing | Verify FOS approve role restricted to DD_ENGINEER in agent prompt |
| `Tier 6j L180 RBAC test not denied` | TRADING approve role check missing | Verify TRADING approve role restricted to MALL_MANAGER/OPERATIONS |
| `Tier 6d/6g/6j lifecycle fails at APPROVE_SR` | No FOS/END/TRADING SR exists to open | These phases use `open_existing_sr=True`; pipeline smoke (0p) must pass first |
| `FOS no-branch card appears without delay_reason` | delay_reason gating removed from prompt | Check FOS agent prompt — `delay_reason` is required (not optional) for FOS |
| `TRADING no-branch blocked on delay_reason` | delay_reason incorrectly required for TRADING | Check TRADING agent prompt — `delay_reason` is optional (should not block) for TRADING |
| `Server health check fails` | Backend not running | Start with `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| `Auth failed for aisha@cenomi.com` | Users not seeded | Run `python scripts/seed_users.py` from `backend/` |
| `alembic.util.exc.CommandError` | DB schema out of date | Run `alembic upgrade head` from `backend/` |
| `workflow_stage: expected 'SR_CREATED', got ''` | Agent did not submit (confirmation never shown) | Check `expect_ui_type="confirmation_card"` turn for failures |
| `Keywords not found` (soft warning) | LLM response variation | Soft warnings don't fail the scenario; check if behavior is functionally correct |

---

## 10. Mock Lease + User Credentials

All scenarios use seeded test data. Do not modify these.

### Lease Codes

| Code | Brand | Mall | City | Unit | Size |
|---|---|---|---|---|---|
| `t0105712` | Under Armour | Jawharat Jeddah | Jeddah | FF050 | 420 sqm |
| `t0208831` | Nike | Riyadh Park | Riyadh | GF101/102 | 680 sqm |
| `t0301144` | Nike | Mall of Arabia | Jeddah | LG220 | 510 sqm |
| `t0419977` | Zara | Dubai Festival City | Dubai | UF301 | 900 sqm |

### Seeded Users

| Email | Password | Role | Stage access |
|---|---|---|---|
| `aisha@cenomi.com` | `test1234` | `MALL_MANAGER` | CREATE_SR, TRADING confirm |
| `khalid@cenomi.com` | `test1234` | `FM_MANAGER` | FM_REVIEW — review + approve |
| `sara@cenomi.com` | `test1234` | `DD_ENGINEER` | RDD_REVIEW — review + final approve; FOS/END confirm |
| `omar@cenomi.com` | `test1234` | `OPERATIONS` | FM_REVIEW (same as FM_MANAGER); read / approve trading |
| `admin@cenomi.com` | `test1234` | `ADMIN` | All |

### RBAC enforcement summary (Handover A)

| Tier | Who tries | What they try | Expected result |
|---|---|---|---|
| 3c | `sara` (DD_ENGINEER) | FM approve action on FM_REVIEW SR | Denied — `["not authorised", "fm", "role"]` |
| 3f | `khalid` (FM_MANAGER) | RDD final approve on RDD_REVIEW SR | Denied — `["not authorised", "rdd", "role"]` |
| 3i | `aisha` (MALL_MANAGER) | FM approve action on FM_REVIEW SR | Denied — role isolation |
| 3i | `khalid` (FM_MANAGER) | RDD submit on RDD_REVIEW SR | Denied — role isolation |
| 5 | Non-MALL_MANAGER | Create Handover B (already-trading) SR | Denied — `["not permitted", "mall manager"]` |

### RBAC enforcement summary (Fitout agents)

| Tier | Who tries | What they try | Expected result |
|---|---|---|---|
| 6c | `aisha` (MALL_MANAGER) | Approve FOS fitout confirmation SR | Denied — `["permission", "not", "allowed"]` |
| 6j | `sara` (DD_ENGINEER) | Approve TRADING fitout confirmation SR | Denied — `["permission", "not", "allowed", "approve", "trading"]` |
