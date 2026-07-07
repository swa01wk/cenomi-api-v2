# RDD Handover Lifecycle — Test Automation Reference

> **Purpose:** Single runnable reference for the full RDD lifecycle test automation,
> covering each role's permitted actions, RBAC denial matrix, RLS property scoping,
> Playwright browser tests, and LLM eval scenarios.
>
> **Five tiers:**
> - **Tier 1 — pytest** — unit + integration tests; run in CI without a live server
> - **Tier 2 — curl / HTTP** — E2E against a live server; copy-paste bash blocks
> - **Tier 3 — RBAC + RLS** — both pytest and curl variants
> - **Tier 4 — Playwright** — browser E2E tests (mocked + live stack)
> - **Tier 5 — Agent eval** — 47 LLM scenarios covering all 3 lifecycle phases
>
> **Upload requirement:** The `/api/upload` endpoint accepts **PDF, JPEG, PNG only**.
> In mock mode (`SERVICE_REQUEST_API_BASE_URL` unset) the upload returns a mock
> document ID automatically — no real file is needed for eval/testing.

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Test Credentials & JWT Acquisition](#2-test-credentials--jwt-acquisition)
3. [Phase 1 — Mall Manager (CREATE_SR)](#3-phase-1--mall-manager-create_sr)
4. [Phase 2 — FM Manager / Operations (FM_REVIEW)](#4-phase-2--fm-manager--operations-fm_review)
5. [Phase 3 — RDD Project Manager (RDD_REVIEW)](#5-phase-3--rdd-project-manager-rdd_review)
6. [RBAC Denial Matrix](#6-rbac-denial-matrix)
7. [RLS Property Scoping](#7-rls-property-scoping)
8. [Full Automated pytest Suite](#8-full-automated-pytest-suite)
9. [Playwright Browser Tests](#9-playwright-browser-tests)
10. [Agent Evaluation — 47 Scenarios](#10-agent-evaluation--47-scenarios)
11. [Observability Export](#11-observability-export)

---

## 1. Quick Start

### Run the entire test suite (no server required)

```bash
cd service-request-chatbot/backend

# Full suite — unit + integration + E2E (mocked externals) — 1,387 tests
pytest tests/unit/ tests/integration/ tests/e2e/ \
  -v --tb=short \
  --ignore=tests/eval \
  2>&1 | tee /tmp/lifecycle_test_results.txt

echo "Exit: $?"
```

### Run only lifecycle + RBAC tests

```bash
pytest tests/ -v --tb=short \
  -k "lifecycle or rbac or role_stage or rdd or fm_review"
```

### Run with coverage

```bash
pytest tests/unit/ tests/integration/ \
  --cov=app --cov-report=term-missing \
  --tb=short -q
```

### Run all 5 tiers (full live stack)

```bash
# Auto-starts Docker, backend, frontend then runs all tiers
cd service-request-chatbot
./run-live-tests.sh

# Or individual tiers:
./run-live-tests.sh --tier unit    # pytest only (no server)
./run-live-tests.sh --tier eval    # 33 CREATE_SR LLM scenarios
./run-live-tests.sh --tier ui      # Playwright browser tests
./run-live-tests.sh --no-start     # stack already running
```

---

## 2. Test Credentials & JWT Acquisition

### Seed users (first time only)

```bash
cd service-request-chatbot/backend
python scripts/seed_users.py
```

### Seeded user matrix

| Role | Email | Password | `unique_property_ids` | `is_global_admin` |
|---|---|---|---|---|
| MALL_MANAGER | aisha@cenomi.com | test1234 | `[3041]` (Jawharat Jeddah) | false |
| FM_MANAGER | khalid@cenomi.com | test1234 | `[3041]` (Jawharat Jeddah) | false |
| OPERATIONS | omar@cenomi.com | test1234 | `[3041]` (Jawharat Jeddah) | false |
| DD_ENGINEER (RDD PM) | sara@cenomi.com | test1234 | `[]` (no property scope) | false |
| ADMIN | admin@cenomi.com | test1234 | `[]` | **true** (global) |

### Acquire all tokens

```bash
#!/usr/bin/env bash
# Save as scripts/get_tokens.sh and source it: source scripts/get_tokens.sh
BASE="http://localhost:8000"

_login() {
  curl -s -X POST "$BASE/api/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$1\",\"password\":\"test1234\"}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['access_token'])"
}

MM_TOKEN=$(_login "aisha@cenomi.com")
FM_TOKEN=$(_login "khalid@cenomi.com")
OPS_TOKEN=$(_login "omar@cenomi.com")
RDD_TOKEN=$(_login "sara@cenomi.com")
ADMIN_TOKEN=$(_login "admin@cenomi.com")

echo "Mall Manager  : ${MM_TOKEN:0:40}..."
echo "FM Manager    : ${FM_TOKEN:0:40}..."
echo "Operations    : ${OPS_TOKEN:0:40}..."
echo "RDD PM        : ${RDD_TOKEN:0:40}..."
echo "Admin         : ${ADMIN_TOKEN:0:40}..."
```

### Decode JWT to verify RBAC + RLS claims

```bash
# Inspect any token — confirms roles, unique_property_ids, mall_names, is_global_admin
_decode_jwt() {
  echo "$1" | cut -d. -f2 \
    | awk '{n=length($0)%4; if(n==2) $0=$0"=="; else if(n==3) $0=$0"="; print}' \
    | base64 -d 2>/dev/null \
    | python3 -m json.tool
}

echo "=== Mall Manager claims ===" && _decode_jwt "$MM_TOKEN"
echo "=== RDD PM claims ===" && _decode_jwt "$RDD_TOKEN"
echo "=== Admin claims ===" && _decode_jwt "$ADMIN_TOKEN"

# Expected Mall Manager output:
# {
#   "sub": "...",
#   "roles": ["MALL_MANAGER"],
#   "unique_property_ids": [3041],
#   "mall_names": ["Jawharat Jeddah"],
#   "is_global_admin": false
# }
```

### Assertion helper

```bash
# Reusable assertion helper — use throughout the script
_assert() {
  local label="$1" actual="$2" expected="$3"
  if [ "$actual" = "$expected" ]; then
    echo "  PASS  [$label] = $expected"
  else
    echo "  FAIL  [$label] expected='$expected' got='$actual'"
  fi
}

# Extract field from JSON response using python3
_field() { echo "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d$2)" 2>/dev/null; }
```

---

## 3. Phase 1 — Mall Manager (CREATE_SR)

> **Role:** `aisha@cenomi.com` (MALL_MANAGER)
> **RLS:** `unique_property_ids=[3041]` — lease lookups scoped to Jawharat Jeddah

### 3a. Full CREATE_SR curl sequence

```bash
#!/usr/bin/env bash
BASE="http://localhost:8000"
CHAT="$BASE/api/chat/service-request"

# ── Turn 1: Start the SR creation ─────────────────────────────────────────
R1=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $MM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"aisha","message":"I want to raise a handover service request"}')
MM_SESSION=$(_field "$R1" "['session_id']")
echo "Session: $MM_SESSION"
_assert "intent" "$(_field "$R1" "['state']['intent']")" "CREATE_RDD_SERVICE_REQUEST"

# ── Turn 2: Provide lease code ────────────────────────────────────────────
R2=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $MM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"aisha\",\"session_id\":\"$MM_SESSION\",
       \"message\":\"The lease code is t0105712\"}")
# Bot should look up the lease and return brand/mall details
echo "$R2" | python3 -m json.tool | grep -E "message|intent|missing"

# ── Turn 3: Provide dates + inspection_done_by ────────────────────────────
R3=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $MM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"aisha\",\"session_id\":\"$MM_SESSION\",
       \"message\":\"Inspection from June 1 2026 to June 10 2026, FM Manager will do it\"}")
echo "$R3" | python3 -m json.tool | grep -E "message|missing"

# ── Turn 4: Provide notes ─────────────────────────────────────────────────
R4=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $MM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"aisha\",\"session_id\":\"$MM_SESSION\",
       \"message\":\"no notes\"}")

# ── Turn 5: Provide comments ──────────────────────────────────────────────
R5=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $MM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"aisha\",\"session_id\":\"$MM_SESSION\",
       \"message\":\"no comments\"}")
# Assert: confirmation card shown
_assert "ui.type" "$(_field "$R5" "['ui']['type']")" "confirmation_card"
_assert "confirmAction" "$(_field "$R5" "['ui']['confirmAction']")" "confirm"

# ── Turn 6: Confirm submission ────────────────────────────────────────────
R6=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $MM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"aisha\",\"session_id\":\"$MM_SESSION\",
       \"action\":\"confirm\"}")
SR_ID=$(_field "$R6" "['state'].get('sr_id','')")
echo "SR_ID: $SR_ID"
_assert "sr_id present" "$([ -n \"$SR_ID\" ] && echo yes || echo no)" "yes"
```

### 3b. pytest — CREATE_SR lifecycle

```bash
# E2E lifecycle test (mocked LLM + Lease + SR APIs)
pytest tests/e2e/test_handover_full_lifecycle_http.py::TestFullHandoverLifecycle -v

# Unit — payload builder for CREATE_SR
pytest tests/unit/ -k "payload_builder or handover_schema" -v
```

---

## 4. Phase 2 — FM Manager / Operations (FM_REVIEW)

> **Roles:** `khalid@cenomi.com` (FM_MANAGER) or `omar@cenomi.com` (OPERATIONS)
> **Prerequisite:** `$SR_ID` and `$MM_SESSION` from Phase 1
> **RLS:** `unique_property_ids=[3041]` — same property scope as Mall Manager

### 4a. Create PDF fixture for upload

```bash
# The upload endpoint requires PDF, JPEG, or PNG (not .txt).
# Create a minimal 1-page PDF using Python:
python3 - <<'EOF'
with open("tests/fixtures/sample_fm_checklist.pdf", "wb") as f:
    content = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R
/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj
4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
5 0 obj<</Length 80>>
stream
BT /F1 12 Tf 72 720 Td (FM Handover Checklist - Sample Test Document) Tj ET
endstream
endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000274 00000 n
0000000352 00000 n
trailer<</Size 6/Root 1 0 R>>
startxref
482
%%EOF"""
    f.write(content)
print("Created: tests/fixtures/sample_fm_checklist.pdf")
EOF
```

### 4b. Full FM_REVIEW curl sequence

```bash
CHAT="http://localhost:8000/api/chat/service-request"
UPLOAD="http://localhost:8000/api/upload"

# ── Turn 1: FM Manager opens existing SR ──────────────────────────────────
R_FM1=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $FM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"khalid\",\"message\":\"Review this SR\",\"sr_id\":\"$SR_ID\"}")
FM_SESSION=$(_field "$R_FM1" "['session_id']")
_assert "workflow_stage" "$(_field "$R_FM1" "['state']['workflow_stage']")" "FM_REVIEW"

# ── Turn 2: Provide unit_readiness_date ───────────────────────────────────
R_FM2=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $FM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"khalid\",\"session_id\":\"$FM_SESSION\",
       \"message\":\"Unit ready July 15 2026\"}")
# Assert expected_handover_date auto-computed = 2026-07-22 (+ 7 days)
echo "=== FM Turn 2 response ===" && echo "$R_FM2" | python3 -m json.tool | grep -E "message|missing"

# ── Turn 3: Provide comments ──────────────────────────────────────────────
R_FM3=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $FM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"khalid\",\"session_id\":\"$FM_SESSION\",
       \"message\":\"All units ready for inspection\"}")

# ── Upload FM document (requires PDF, JPEG, or PNG) ───────────────────────
R_UPLOAD=$(curl -s -X POST "$UPLOAD" \
  -H "Authorization: Bearer $FM_TOKEN" \
  -F "file=@tests/fixtures/sample_fm_checklist.pdf" \
  -F "document_type=SR_HANDOVER_CHECKLIST" \
  -F "session_id=$FM_SESSION" \
  -F "sr_id=$SR_ID")
FM_DOC_ID=$(_field "$R_UPLOAD" "['document_id']")
echo "FM Doc ID: $FM_DOC_ID"
_assert "upload status" "$(_field "$R_UPLOAD" "['status']")" "uploaded"

# ── Turn 4: Click Save Progress → get confirmation card ───────────────────
R_FM4=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $FM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"khalid\",\"session_id\":\"$FM_SESSION\",
       \"action\":\"save_fm_progress\",\"message\":\"\"}")
_assert "ui.type" "$(_field "$R_FM4" "['ui']['type']")" "confirmation_card"
_assert "confirmAction" "$(_field "$R_FM4" "['ui']['confirmAction']")" "confirm_fm_save"

# ── Turn 5: Confirm FM save (single-turn action) ──────────────────────────
R_FM5=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $FM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"khalid\",\"session_id\":\"$FM_SESSION\",
       \"action\":\"confirm_fm_save\",\"message\":\"\"}")
_assert "fm_status" "$(_field "$R_FM5" "['state']['fm_status']")" "IN_PROCESS"

# ── Turn 6: Click Approve Review → get confirmation card ──────────────────
R_FM6=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $FM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"khalid\",\"session_id\":\"$FM_SESSION\",
       \"action\":\"approve_fm_review\",\"message\":\"\"}")
_assert "confirmAction" "$(_field "$R_FM6" "['ui']['confirmAction']")" "confirm_fm_approve"

# ── Turn 7: Confirm FM approve ────────────────────────────────────────────
R_FM7=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $FM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"khalid\",\"session_id\":\"$FM_SESSION\",
       \"action\":\"confirm_fm_approve\",\"message\":\"\"}")
echo "FM workflow_stage after approve: $(_field "$R_FM7" "['state']['workflow_stage']")"
# Platform drives to RDD_REVIEW on next sr_status_sync
```

### 4c. pytest — FM_REVIEW RBAC

```bash
# FM_REVIEW entry node role access matrix
pytest tests/integration/test_role_stage_access.py::TestFMReviewEntryRoleAccess -v

# Includes: FM_MANAGER allowed, OPERATIONS allowed, MALL_MANAGER denied, DD_ENGINEER denied
```

### 4d. Operations role — identical to FM_MANAGER

```bash
# Operations has the same FM_REVIEW permissions as FM_MANAGER
# Re-run turns 1–7 with $OPS_TOKEN to verify equivalence

R_OPS1=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $OPS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"omar\",\"message\":\"Review SR\",\"sr_id\":\"$SR_ID\"}")
OPS_SESSION=$(_field "$R_OPS1" "['session_id']")
_assert "ops workflow_stage" "$(_field "$R_OPS1" "['state']['workflow_stage']")" "FM_REVIEW"
```

---

## 5. Phase 3 — RDD Project Manager (RDD_REVIEW)

> **Role:** `sara@cenomi.com` (DD_ENGINEER / RDD Project Manager)
> **Prerequisite:** SR in `RDD_REVIEW` stage (after FM Manager approved in Phase 2)
> **RLS:** `unique_property_ids=[]` — RDD PM has no lease-lookup restriction (reviewer only)

### 5a. Create PDF fixture for handover report upload

```bash
python3 - <<'EOF'
with open("tests/fixtures/sample_rdd_handover_report.pdf", "wb") as f:
    content = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R
/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj
4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
5 0 obj<</Length 74>>
stream
BT /F1 12 Tf 72 720 Td (RDD Handover Meeting Report - Sample Test Document) Tj ET
endstream
endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000274 00000 n
0000000352 00000 n
trailer<</Size 6/Root 1 0 R>>
startxref
476
%%EOF"""
    f.write(content)
print("Created: tests/fixtures/sample_rdd_handover_report.pdf")
EOF
```

### 5b. Full RDD_REVIEW curl sequence

```bash
CHAT="http://localhost:8000/api/chat/service-request"
UPLOAD="http://localhost:8000/api/upload"

# ── Turn 1: RDD PM opens existing SR ──────────────────────────────────────
R_RDD1=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $RDD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"sara\",
       \"message\":\"I want to review this handover report\",
       \"sr_id\":\"$SR_ID\"}")
RDD_SESSION=$(_field "$R_RDD1" "['session_id']")
_assert "workflow_stage" "$(_field "$R_RDD1" "['state']['workflow_stage']")" "RDD_REVIEW"

# ── Turn 2: Provide dates + guideline ─────────────────────────────────────
R_RDD2=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $RDD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"sara\",\"session_id\":\"$RDD_SESSION\",
       \"message\":\"Actual handover July 15 2026, fitout starts July 16, fitout ends July 22, guidelines: https://cenomi.com/guidelines/handover\"}")
# Assert trading_date auto-computed = 2026-07-30 (July 22 + 8 days)
echo "=== RDD Turn 2 ===" && echo "$R_RDD2" | python3 -m json.tool | grep -E "message|missing"

# ── Turn 3: Provide document_status ───────────────────────────────────────
R_RDD3=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $RDD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"sara\",\"session_id\":\"$RDD_SESSION\",
       \"message\":\"Approve\"}")
# Bot extracts document_status = "APPROVED"

# ── Turn 4: Provide comments ──────────────────────────────────────────────
R_RDD4=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $RDD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"sara\",\"session_id\":\"$RDD_SESSION\",
       \"message\":\"Handover completed successfully. All documentation verified.\"}")

# ── Upload RDD handover report (mandatory — must be PDF/JPEG/PNG) ─────────
R_RDD_UPLOAD=$(curl -s -X POST "$UPLOAD" \
  -H "Authorization: Bearer $RDD_TOKEN" \
  -F "file=@tests/fixtures/sample_rdd_handover_report.pdf" \
  -F "document_type=DR_SR_HANDOVER_REPORT" \
  -F "session_id=$RDD_SESSION" \
  -F "sr_id=$SR_ID")
RDD_DOC_ID=$(_field "$R_RDD_UPLOAD" "['document_id']")
echo "RDD Doc ID: $RDD_DOC_ID"
_assert "rdd upload status" "$(_field "$R_RDD_UPLOAD" "['status']")" "uploaded"

# ── Turn 5: Click Submit Report → get confirmation card ───────────────────
R_RDD5=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $RDD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"sara\",\"session_id\":\"$RDD_SESSION\",
       \"action\":\"submit_rdd_report\",\"message\":\"\"}")
_assert "ui.type" "$(_field "$R_RDD5" "['ui']['type']")" "confirmation_card"
_assert "confirmAction" "$(_field "$R_RDD5" "['ui']['confirmAction']")" "confirm_rdd_submit"

# Verify trading_date is read-only (auto-computed) in the confirmation card fields:
echo "trading_date on card:"
echo "$R_RDD5" | python3 -c "
import sys,json
d=json.load(sys.stdin)
fields=d.get('ui',{}).get('fields',[])
for f in fields:
    if f['key']=='trading_date':
        print(f'  value={f[\"value\"]}  editable={f.get(\"editable\",False)}')
"
# Expected: value="2026-07-30"  editable=False

# Verify document_status has options on the confirmation card:
echo "document_status on card:"
echo "$R_RDD5" | python3 -c "
import sys,json
d=json.load(sys.stdin)
fields=d.get('ui',{}).get('fields',[])
for f in fields:
    if f['key']=='document_status':
        print(f'  value={f[\"value\"]}  options={f.get(\"options\")}')
"
# Expected: value="APPROVED"  options=["APPROVED", "APPROVED_WITH_CHANGES"]

# ── Turn 6: Confirm RDD submit (single-turn) ──────────────────────────────
R_RDD6=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $RDD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"sara\",\"session_id\":\"$RDD_SESSION\",
       \"action\":\"confirm_rdd_submit\",\"message\":\"\"}")
_assert "rdd_status" "$(_field "$R_RDD6" "['state']['rdd_status']")" "REPORT_SUBMITTED"
_assert "workflow_stage" "$(_field "$R_RDD6" "['state']['workflow_stage']")" "RDD_REVIEW"

# ── Turn 7: Click Final Approve → get confirmation card ───────────────────
R_RDD7=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $RDD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"sara\",\"session_id\":\"$RDD_SESSION\",
       \"action\":\"approve_rdd_final\",\"message\":\"\"}")
_assert "confirmAction" "$(_field "$R_RDD7" "['ui']['confirmAction']")" "confirm_rdd_final_approve"

# ── Turn 8: Confirm final approve ─────────────────────────────────────────
R_RDD8=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $RDD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"sara\",\"session_id\":\"$RDD_SESSION\",
       \"action\":\"confirm_rdd_final_approve\",\"message\":\"\"}")
_assert "workflow_stage SR_COMPLETED" "$(_field "$R_RDD8" "['state']['workflow_stage']")" "SR_COMPLETED"
_assert "rdd_status APPROVED" "$(_field "$R_RDD8" "['state']['rdd_status']")" "APPROVED"

echo ""
echo "=== LIFECYCLE COMPLETE ==="
echo "SR $SR_ID is now SR_COMPLETED with rdd_status=APPROVED"
```

### 5c. pytest — RDD_REVIEW RBAC and lifecycle

```bash
# RDD entry node role access matrix
pytest tests/integration/test_role_stage_access.py::TestRDDReviewEntryRoleAccess -v

# RDD API submission — role enforcement (submit + final approve)
pytest tests/integration/test_role_stage_access.py::TestRDDApiSubmitRoleAccess -v
pytest tests/integration/test_role_stage_access.py::TestRDDApiFinalApproveRoleAccess -v

# RDD node unit tests (payload builder, date format, rdd_action guard)
pytest tests/unit/test_rdd_nodes.py -v

# RDD final approve fix (two-step Phase 3)
pytest tests/unit/test_rdd_final_approve.py -v

# Full E2E lifecycle (mocked)
pytest tests/e2e/test_handover_full_lifecycle_http.py -v
```

---

## 6. RBAC Denial Matrix

### 6a. RBAC role/action permission map

```
Role           | CREATE_SR | FM_REVIEW | RDD_REVIEW | Upload FM | Upload RDD
─────────────────────────────────────────────────────────────────────────────
MALL_MANAGER   |   PASS    |   DENY    |    DENY    |   DENY    |    DENY
FM_MANAGER     |   DENY    |   PASS    |    DENY    |   PASS    |    DENY
OPERATIONS     |   DENY    |   PASS    |    DENY    |   PASS    |    DENY
DD_ENGINEER    |   DENY    |   DENY    |    PASS    |   DENY    |    PASS
ADMIN          |   PASS    |   PASS    |    PASS    |   PASS    |    PASS
Unauthenticated|   401     |   401     |    401     |   401     |    401
```

### 6b. pytest — full RBAC matrix

```bash
# Role-to-stage access matrix (all entries in the table above)
pytest tests/integration/test_role_stage_access.py -v

# Permission service — action → permission string mapping
pytest tests/unit/test_permission_service.py -v
pytest tests/unit/test_permission_service_extended.py -v

# Role permission map — role → frozenset of permissions
pytest tests/unit/test_role_permission_map.py -v

# Supervisor routing — intent gating per role
pytest tests/unit/test_supervisor_routing.py -v

# Help agent schema — ROLE_PERMITTED_INTENTS
pytest tests/unit/test_help_agent_schema.py -v
```

### 6c. curl — RBAC denial checks

#### Mall Manager denied FM and RDD actions

```bash
# Mall Manager tries to do FM save progress on an existing SR
R_DENY1=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $MM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"aisha\",\"message\":\"\",\"sr_id\":\"$SR_ID\",
       \"action\":\"save_fm_progress\"}")
STATUS1=$(_field "$R_DENY1" "['state']['workflow_stage']")
MSG1=$(_field "$R_DENY1" "['message']")
echo "MALL_MANAGER save_fm_progress → message: $MSG1"
# Expected: message contains "permission" or "FM Manager or Operations role"

# Mall Manager tries to submit RDD report
R_DENY2=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $MM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"aisha\",\"message\":\"\",\"sr_id\":\"$SR_ID\",
       \"action\":\"submit_rdd_report\"}")
echo "MALL_MANAGER submit_rdd_report → message: $(_field "$R_DENY2" "['message']")"
# Expected: "RDD Project Manager role" denial message
```

#### FM Manager denied RDD actions

```bash
# FM Manager tries to submit RDD report
R_DENY3=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $FM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"khalid\",\"message\":\"\",\"sr_id\":\"$SR_ID\",
       \"action\":\"submit_rdd_report\"}")
echo "FM_MANAGER submit_rdd_report → message: $(_field "$R_DENY3" "['message']")"
# Expected: "RDD Project Manager role" denial

# FM Manager tries to create a new SR (intent not permitted)
R_DENY4=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $FM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"khalid","message":"I want to create a new handover service request"}')
echo "FM_MANAGER create SR → intent: $(_field "$R_DENY4" "['state']['intent']")"
# Expected: intent denied; supervisor routes elsewhere (not CREATE_RDD_SERVICE_REQUEST)
```

#### RDD Project Manager denied CREATE and FM actions

```bash
# RDD PM tries to create SR (no create intent)
R_DENY5=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $RDD_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"sara","message":"I want to create a handover service request"}')
echo "RDD_PM create SR → intent: $(_field "$R_DENY5" "['state']['intent']")"
# Expected: no CREATE_RDD_SERVICE_REQUEST intent; routes to FAQ

# RDD PM tries FM approval
R_DENY6=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $RDD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"sara\",\"message\":\"\",\"sr_id\":\"$SR_ID\",
       \"action\":\"approve_fm_review\"}")
echo "RDD_PM approve_fm_review → message: $(_field "$R_DENY6" "['message']")"
# Expected: "FM Manager or Operations role" denial
```

#### Unauthenticated with RBAC_ENFORCE=true

```bash
# Ensure RBAC_ENFORCE=true in backend/.env, then restart backend
R_UNAUTH=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$CHAT" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"anon","message":"hello"}')
_assert "unauthenticated → 401" "$R_UNAUTH" "401"

# Upload endpoint also requires auth
R_UNAUTH_UPLOAD=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  "http://localhost:8000/api/upload" \
  -F "file=@tests/fixtures/sample_fm_checklist.pdf" \
  -F "document_type=SR_HANDOVER_CHECKLIST")
_assert "unauthenticated upload → 401" "$R_UNAUTH_UPLOAD" "401"
```

#### RBAC on document upload endpoint

```bash
# Mall Manager cannot upload FM documents (needs CAN_FM_REVIEW_HANDOVER_SR)
R_UPLOAD_DENY=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "http://localhost:8000/api/upload" \
  -H "Authorization: Bearer $MM_TOKEN" \
  -F "file=@tests/fixtures/sample_fm_checklist.pdf" \
  -F "document_type=SR_HANDOVER_CHECKLIST" \
  -F "sr_id=$SR_ID")
_assert "MALL_MANAGER upload FM doc → 403" "$R_UPLOAD_DENY" "403"

# RDD PM cannot upload FM documents (needs CAN_RDD_REVIEW_HANDOVER_SR, not FM)
R_UPLOAD_DENY2=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "http://localhost:8000/api/upload" \
  -H "Authorization: Bearer $RDD_TOKEN" \
  -F "file=@tests/fixtures/sample_fm_checklist.pdf" \
  -F "document_type=SR_HANDOVER_CHECKLIST" \
  -F "sr_id=$SR_ID")
_assert "RDD_PM upload FM doc → 403" "$R_UPLOAD_DENY2" "403"
```

---

## 7. RLS Property Scoping

### 7a. What RLS means in this system

`AuthContext.unique_property_ids` (tuple of ints from JWT) scopes which leases a user can look up.
It is populated at login from `users.unique_property_ids` in the DB.

| Claim | Effect |
|---|---|
| `unique_property_ids: [3041]` | Lease lookup filtered to property 3041 (Jawharat Jeddah) |
| `unique_property_ids: []` with `is_global_admin: false` | No properties — lease lookup returns empty |
| `is_global_admin: true` | Bypasses all property filters — sees all leases |

### 7b. Verify RLS claims in JWT

```bash
# Mall Manager — scoped to Jawharat Jeddah (property 3041)
echo "=== MALL_MANAGER RLS ===" && _decode_jwt "$MM_TOKEN" | grep -E "unique_property|mall_names|is_global"
# Expected: "unique_property_ids": [3041], "mall_names": ["Jawharat Jeddah"]

# RDD Project Manager — no property scope (reviews only, does not look up leases)
echo "=== RDD_PM RLS ===" && _decode_jwt "$RDD_TOKEN" | grep -E "unique_property|mall_names|is_global"
# Expected: "unique_property_ids": [], "mall_names": []

# Admin — global admin, bypasses all RLS
echo "=== ADMIN RLS ===" && _decode_jwt "$ADMIN_TOKEN" | grep -E "unique_property|mall_names|is_global"
# Expected: "is_global_admin": true
```

### 7c. Seed a restricted user for RLS denial test

Add to `scripts/seed_users.py` or run once:

```python
# Insert via psql or seed script:
{
    "username": "restricted@cenomi.com",
    "email": "restricted@cenomi.com",
    "password": "test1234",
    "role": "MALL_MANAGER",
    "unique_property_ids": [],    # No properties — lease lookup returns empty
    "mall_names": [],
    "is_global_admin": False,
}
```

Or via psql:

```sql
INSERT INTO users (username, email, password_hash, role, unique_property_ids, mall_names, is_global_admin, is_active)
VALUES (
  'restricted@cenomi.com',
  'restricted@cenomi.com',
  -- bcrypt hash of "test1234" — use `python3 -c "import bcrypt; print(bcrypt.hashpw(b'test1234', bcrypt.gensalt()).decode())"`
  '<HASH>',
  'MALL_MANAGER',
  '[]',
  '[]',
  false,
  true
);
```

### 7d. curl — RLS denial test

```bash
# Acquire token for restricted user (no property IDs)
RESTRICTED_TOKEN=$(_login "restricted@cenomi.com")

# Verify: empty property scope
echo "=== Restricted user JWT ===" && _decode_jwt "$RESTRICTED_TOKEN" | grep -E "unique_property|mall_names|is_global"
# Expected: "unique_property_ids": [], "mall_names": []

# Try to create SR — lease lookup will return empty
R_RLS1=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $RESTRICTED_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"restricted","message":"I want to raise a handover SR for lease t0105712"}')
MSG_RLS1=$(_field "$R_RLS1" "['message']")
echo "Restricted user lease lookup result: $MSG_RLS1"
# Expected: bot asks for lease code again (lease lookup returned empty)
# The confirmation card is NEVER reached without a valid lease
```

### 7e. curl — Admin bypasses RLS

```bash
# Admin can look up any lease regardless of property scoping
R_ADMIN=$(curl -s -X POST "$CHAT" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"admin","message":"I want to raise a handover SR for lease t0105712"}')
echo "Admin lease lookup result: $(_field "$R_ADMIN" "['message']")"
# Expected: bot finds the lease and proceeds with SR creation
```

### 7f. pytest — RLS AuthContext

```bash
# AuthContext carries RLS fields correctly
python3 -c "
from app.types.chat import AuthContext
# Scoped user
ctx = AuthContext(
    subject_id='test',
    roles=frozenset({'CAN_RAISE_HANDOVER_SR'}),
    unique_property_ids=(3041,),
    mall_names=('Jawharat Jeddah',),
    is_global_admin=False,
)
assert ctx.unique_property_ids == (3041,)
assert ctx.is_global_admin == False
print('PASS: Scoped AuthContext')

# Global admin
admin_ctx = AuthContext(
    subject_id='admin',
    roles=frozenset({'CAN_RAISE_HANDOVER_SR'}),
    unique_property_ids=(),
    mall_names=(),
    is_global_admin=True,
)
assert admin_ctx.is_global_admin == True
print('PASS: Global admin AuthContext')
"

# Security: get_auth_context correctly maps JWT → AuthContext
pytest tests/unit/ -k "auth or security" -v
```

---

## 8. Full Automated pytest Suite

### 8a. One-command full suite

```bash
cd service-request-chatbot/backend

pytest \
  tests/unit/ \
  tests/integration/ \
  tests/e2e/ \
  -v \
  --tb=short \
  --ignore=tests/eval \
  -x \
  2>&1 | tee /tmp/lifecycle_test_results.txt

echo ""
echo "=== RESULT ==="
echo "Exit code: $?"
grep -E "passed|failed|error" /tmp/lifecycle_test_results.txt | tail -3
```

### 8b. Test categories and commands

```bash
# ── Unit tests ─────────────────────────────────────────────────────────────
pytest tests/unit/test_rdd_nodes.py -v                      # RDD Phase 3 nodes
pytest tests/unit/test_rdd_final_approve.py -v              # Two-step RDD flow
pytest tests/unit/test_permission_service.py -v             # Permission service
pytest tests/unit/test_permission_service_extended.py -v    # Extended perms
pytest tests/unit/test_role_permission_map.py -v            # Role→perm map
pytest tests/unit/test_supervisor_routing.py -v             # Intent gating
pytest tests/unit/test_help_agent_schema.py -v              # ROLE_PERMITTED_INTENTS
pytest tests/unit/test_handover_schema.py -v                # Schema validation
pytest tests/unit/test_validation_service.py -v             # Date chain etc.
pytest tests/unit/test_confirmation_node.py -v              # Confirmation card
pytest tests/unit/test_workflow_config.py -v                # WorkflowConfig registry
pytest tests/unit/test_document_upload_node.py -v           # Document bucketing
pytest tests/unit/test_document_count_validation.py -v      # Upload requirements

# ── Integration tests ──────────────────────────────────────────────────────
pytest tests/integration/test_role_stage_access.py -v       # RBAC matrix
pytest tests/integration/test_fm_review_e2e.py -v           # FM lifecycle
pytest tests/integration/test_rdd_review_e2e.py -v          # RDD lifecycle
pytest tests/integration/test_handover_lifecycle.py -v      # Full handover
pytest tests/integration/test_service_request_graph.py -v   # Graph wiring
pytest tests/integration/test_chat_endpoint.py -v           # HTTP endpoint
pytest tests/integration/test_trace_lifecycle.py -v         # Observability

# ── E2E tests ──────────────────────────────────────────────────────────────
pytest tests/e2e/test_handover_full_lifecycle_http.py -v    # 9-step lifecycle
pytest tests/e2e/test_handover_sr_e2e.py -v                 # CREATE_SR E2E
```

### 8c. CI-ready invocation (no database, all mocked)

```bash
cd service-request-chatbot/backend

# Set minimal env for tests (in-memory SQLite or skip DB via mock)
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/service_request_chatbot_test"
export RBAC_ENFORCE=true
export OPENAI_API_KEY=sk-test-key   # mocked in tests

pytest tests/ \
  --ignore=tests/eval \
  -q \
  --tb=short \
  --no-header \
  2>&1
```

### 8d. Watch mode during development

```bash
# Re-run tests on file change (requires pytest-watch)
pip install pytest-watch
cd service-request-chatbot/backend
ptw tests/unit/ tests/integration/ -- -v --tb=short -k "rdd or fm or rbac"
```

---

## Appendix — Key Files

| File | Purpose |
|---|---|
| [`tests/integration/test_role_stage_access.py`](../backend/tests/integration/test_role_stage_access.py) | RBAC matrix — all role/stage combinations |
| [`tests/e2e/test_handover_full_lifecycle_http.py`](../backend/tests/e2e/test_handover_full_lifecycle_http.py) | Full 9-turn HTTP lifecycle (mocked) |
| [`tests/unit/test_rdd_nodes.py`](../backend/tests/unit/test_rdd_nodes.py) | RDD Phase 3 node unit tests |
| [`tests/unit/test_permission_service.py`](../backend/tests/unit/test_permission_service.py) | Permission enforcement |
| [`tests/eval/scenarios.py`](../backend/tests/eval/scenarios.py) | 33 CREATE_SR eval scenarios |
| [`tests/eval/lifecycle_scenarios.py`](../backend/tests/eval/lifecycle_scenarios.py) | 14 FM/RDD/full-lifecycle eval scenarios (S34–S47) |
| [`tests/eval/run_eval.py`](../backend/tests/eval/run_eval.py) | CREATE_SR eval runner |
| [`tests/eval/run_lifecycle_eval.py`](../backend/tests/eval/run_lifecycle_eval.py) | Lifecycle eval runner (multi-user, multi-phase) |
| [`app/types/chat.py`](../backend/app/types/chat.py) | `AuthContext` — carries `unique_property_ids` + `mall_names` |
| [`app/api/routes/auth.py`](../backend/app/api/routes/auth.py) | `/api/auth/login` — JWT issuance with RLS claims |
| [`app/api/routes/upload.py`](../backend/app/api/routes/upload.py) | `/api/upload` — RBAC-gated document upload; mock mode auto-returns document ID |
| [`app/agents/services/permission_service.py`](../backend/app/agents/services/permission_service.py) | `ACTION_PERMISSION_MAP` + `ROLE_PERMISSION_MAP`; JWT role names expanded to permissions |
| [`scripts/seed_users.py`](../backend/scripts/seed_users.py) | Seed users with RLS claims |
| [`scripts/export_session_observability.py`](../backend/scripts/export_session_observability.py) | Export session state transitions + eval scores to JSON/Markdown |
| [`scripts/get_session_conversation.py`](../backend/scripts/get_session_conversation.py) | View full conversation transcript + state transitions for any session |

---

## 9. Playwright Browser Tests

### 9a. Mocked tests (no server required)

```bash
cd service-request-chatbot/frontend

# Run all 42 mocked tests
npm run test:e2e

# Interactive UI mode
npm run test:e2e:ui

# Specific file
npx playwright test tests/e2e/auth.spec.ts
npx playwright test tests/e2e/stage-actions.spec.ts
npx playwright test tests/e2e/lifecycle-stepper.spec.ts
npx playwright test tests/e2e/handover-lifecycle.spec.ts
```

All API calls are mocked via `page.route()` — no backend, no LLM costs.

### 9b. Live stack tests

Requires: backend on `:8000`, frontend on `:3000`, Postgres + Redis running.

```bash
cd service-request-chatbot/frontend

# All live tests
npm run test:live

# Just the browser lifecycle (Phases 1–3)
npm run test:live:lifecycle

# Just the eval scenarios
npm run test:live:eval

# Interactive UI mode
npm run test:live:ui
```

### 9c. What each spec covers

| Spec | Tests | Coverage |
|---|---|---|
| `auth.spec.ts` | 8 | Login, credentials, redirect, role badge, sign-out |
| `lifecycle-stepper.spec.ts` | 8 | Stepper renders at each stage, green checks, hidden until SR exists |
| `stage-actions.spec.ts` | 13 | FM/RDD buttons appear/disappear, RBAC denials, disabled state |
| `handover-lifecycle.spec.ts` | 13 | Full CREATE→FM→RDD→SR_COMPLETED, session continuity, SR ID propagation |
| `lifecycle-live.spec.ts` | 16 | Phases 1–3 with real LLM, debug panel, RBAC denials |
| `agent-eval.spec.ts` | 34 | 33 CREATE_SR eval scenarios + @summary |
| `lifecycle-eval.spec.ts` | 15 | 14 FM/RDD/lifecycle scenarios + @lifecycle-summary |

---

## 10. Agent Evaluation — 47 Scenarios

### 10a. CREATE_SR scenarios (S1–S33)

```bash
cd service-request-chatbot/backend

# Run all 33 scenarios
python -m tests.eval.run_eval --verbose

# Filter by tag
python -m tests.eval.run_eval --tags happy-path,core

# Specific scenarios
python -m tests.eval.run_eval --scenarios 1,3,6

# Write JSON report
python -m tests.eval.run_eval --verbose > /tmp/eval_results.txt
```

### 10b. FM_REVIEW + RDD_REVIEW scenarios (S34–S47)

```bash
# Run all 14 lifecycle scenarios
python -m tests.eval.run_lifecycle_eval --verbose

# FM_REVIEW only
python -m tests.eval.run_lifecycle_eval --tags fm-review

# RDD_REVIEW only
python -m tests.eval.run_lifecycle_eval --tags rdd-review

# Full lifecycle (S46–S47)
python -m tests.eval.run_lifecycle_eval --tags full-lifecycle

# With JSON output
python -m tests.eval.run_lifecycle_eval --json results/lifecycle.json
```

### 10c. Scenario coverage matrix

| # | Scenario | Phase | Tags |
|---|---|---|---|
| 1–5 | Happy paths, brand/mall search, one-shot, partial search, correction | CREATE_SR | happy-path, core |
| 6–9 | Invalid lease, cancel/restart, ambiguous, natural language dates | CREATE_SR | error-handling |
| 10–15 | Date validation, garbage dates, inspector ambiguity, off-topic | CREATE_SR | date-validation, edge-case |
| 16–22 | API corrected_fields, selected_lease_id, title auto-gen | CREATE_SR | api |
| 23–33 | Multi-field combos (17A–17D), date violations, multi-SR, injection | CREATE_SR | multi-field-extraction, security |
| 34–36 | FM happy path, save+approve, natural language date | FM_REVIEW | fm-review |
| 37–39 | FM date+comments, RBAC DD denied, Operations role | FM_REVIEW | fm-review, rbac |
| 40–42 | RDD happy path, field-by-field, date chain validation | RDD_REVIEW | rdd-review |
| 43–45 | APPROVED_WITH_CHANGES, natural language dates, RBAC FM denied | RDD_REVIEW | rdd-review, rbac |
| 46–47 | Full lifecycle, lifecycle with corrections | All phases | full-lifecycle |

---

## 11. Observability Export

After running eval scenarios, export full state-transition data for monitoring and further analysis:

```bash
cd service-request-chatbot/backend

# Export specific session IDs
python scripts/export_session_observability.py \
  --session-id <uuid1> <uuid2> \
  --out ./sessions --eval

# Auto-discover last 10 sessions
python scripts/export_session_observability.py \
  --recent 10 --out ./sessions --eval

# From Playwright session-ids.json manifest
python scripts/export_session_observability.py \
  --session-file ../frontend/playwright-report-live/session-ids.json \
  --out ./sessions --eval

# View conversation transcript for a session
python scripts/get_session_conversation.py --session-id <uuid>

# Transitions-only (no dialogue)
python scripts/get_session_conversation.py --session-id <uuid> --transitions-only

# JSON output for programmatic use
python scripts/get_session_conversation.py --session-id <uuid> --json
```

### Export output structure

```
sessions/
  {session_id}.json    — state transitions, field extraction, LLM calls, eval scores
  {session_id}.md      — human-readable transcript
  manifest.ndjson      — one-line summary per session for dashboards
```

Each `.json` export contains:
- `state_transitions[]` — turn-by-turn: stage_before/after, newly_extracted_fields, nodes_executed, llm_calls, tool_calls, latency_ms
- `sr_lifecycle` — timestamps for CREATE, FM_REVIEW, FM_APPROVED, RDD_REVIEW, REPORT_SUBMITTED, SR_COMPLETED
- `eval_score` — PASS/WARN/FAIL counts from the quality evaluator
