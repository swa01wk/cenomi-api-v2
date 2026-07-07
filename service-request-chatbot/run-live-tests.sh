#!/usr/bin/env bash
# run-live-tests.sh — Full live test orchestrator
#
# Starts the entire stack (Postgres, Redis, backend, frontend),
# runs all test tiers in order, then prints a combined summary.
#
# Usage:
#   ./run-live-tests.sh                    # all tiers
#   ./run-live-tests.sh --tier unit        # tier 1 only (no server needed)
#   ./run-live-tests.sh --tier eval        # agent eval only (33 scenarios)
#   ./run-live-tests.sh --tier ui          # Playwright live UI tests only
#   ./run-live-tests.sh --tier all         # explicit: all tiers
#   ./run-live-tests.sh --no-start         # skip docker/server start (already running)
#   ./run-live-tests.sh --scenarios 1,3,6  # pass to run_eval.py
#   ./run-live-tests.sh --tags happy-path  # filter eval by tag
#
# Tiers:
#   1 — pytest unit + integration + mocked E2E      (no live stack needed)
#   2 — pytest eval runner (33 LLM scenarios)       (needs live backend)
#   3 — pytest manual blocks eval                   (needs live backend + seeded users)
#   4 — Playwright live UI tests (3-phase lifecycle)(needs full stack)
#   5 — Playwright agent-eval (33 scenarios via API)(needs live backend)
#
# Exit codes:
#   0 — all selected tiers passed
#   1 — one or more tiers failed

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}✓${RESET} $*"; }
fail() { echo -e "${RED}✗${RESET} $*"; }
info() { echo -e "${CYAN}▶${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET} $*"; }
sep()  { echo -e "${BOLD}$(printf '─%.0s' {1..72})${RESET}"; }

# ── Argument parsing ──────────────────────────────────────────────────────────
TIER="all"
NO_START=false
SCENARIOS_FILTER=""
TAGS_FILTER=""
VERBOSE=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --tier)         TIER="$2";             shift 2 ;;
    --no-start)     NO_START=true;         shift   ;;
    --scenarios)    SCENARIOS_FILTER="$2"; shift 2 ;;
    --tags)         TAGS_FILTER="$2";      shift 2 ;;
    --verbose|-v)   VERBOSE="--verbose";   shift   ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/service-request-chatbot/backend"
FRONTEND_DIR="$SCRIPT_DIR/service-request-chatbot/frontend"
CHATBOT_DIR="$SCRIPT_DIR/service-request-chatbot"

# Results tracking
declare -A TIER_RESULTS
declare -A TIER_TIMES
OVERALL_EXIT=0

run_tier() {
  local name="$1" cmd="$2" dir="$3"
  sep
  info "Tier: $name"
  echo ""
  local t0; t0=$(date +%s)
  if (cd "$dir" && eval "$cmd"); then
    local t1; t1=$(date +%s)
    TIER_RESULTS[$name]="PASSED"
    TIER_TIMES[$name]=$(( t1 - t0 ))
    ok "$name passed ($(( t1 - t0 ))s)"
  else
    local t1; t1=$(date +%s)
    TIER_RESULTS[$name]="FAILED"
    TIER_TIMES[$name]=$(( t1 - t0 ))
    fail "$name FAILED ($(( t1 - t0 ))s)"
    OVERALL_EXIT=1
  fi
  echo ""
}

# ── Stack startup ─────────────────────────────────────────────────────────────
start_stack() {
  if [[ "$NO_START" == true ]]; then
    info "Skipping stack startup (--no-start)"
    return
  fi

  sep
  info "Starting infrastructure (Postgres + Redis)…"
  (cd "$CHATBOT_DIR" && docker compose up -d)

  info "Waiting for Postgres to be healthy…"
  local retries=30
  while [[ $retries -gt 0 ]]; do
    if (cd "$CHATBOT_DIR" && docker compose ps | grep -q "healthy"); then
      ok "Postgres + Redis healthy"
      break
    fi
    sleep 2; retries=$(( retries - 1 ))
  done
  if [[ $retries -eq 0 ]]; then
    warn "Containers may not be fully healthy — continuing anyway"
  fi

  info "Running DB migrations…"
  (cd "$BACKEND_DIR" && source .venv/bin/activate && alembic upgrade head)

  info "Seeding test users…"
  (cd "$BACKEND_DIR" && source .venv/bin/activate && python scripts/seed_users.py) || true

  info "Starting backend (port 8000)…"
  (cd "$BACKEND_DIR" && source .venv/bin/activate && \
    uvicorn app.main:app --port 8000 --log-level warning) &
  BACKEND_PID=$!
  echo "Backend PID: $BACKEND_PID"

  info "Waiting for backend to be ready…"
  retries=30
  while [[ $retries -gt 0 ]]; do
    if curl -sf http://localhost:8000/api/v1/health > /dev/null 2>&1; then
      ok "Backend ready"
      break
    fi
    sleep 2; retries=$(( retries - 1 ))
  done
  if [[ $retries -eq 0 ]]; then
    fail "Backend did not become ready in time"
    exit 1
  fi

  info "Starting frontend (port 3000)…"
  (cd "$FRONTEND_DIR" && npm run dev -- --port 3000) &
  FRONTEND_PID=$!
  echo "Frontend PID: $FRONTEND_PID"

  info "Waiting for frontend to be ready…"
  retries=30
  while [[ $retries -gt 0 ]]; do
    if curl -sf http://localhost:3000 > /dev/null 2>&1; then
      ok "Frontend ready"
      break
    fi
    sleep 2; retries=$(( retries - 1 ))
  done
  if [[ $retries -eq 0 ]]; then
    fail "Frontend did not become ready in time"
    exit 1
  fi
}

# ── Cleanup ───────────────────────────────────────────────────────────────────
cleanup() {
  if [[ "${BACKEND_PID:-}" != "" ]]; then
    info "Stopping backend (PID $BACKEND_PID)…"
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
  if [[ "${FRONTEND_PID:-}" != "" ]]; then
    info "Stopping frontend (PID $FRONTEND_PID)…"
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

BACKEND_PID=""
FRONTEND_PID=""

# ── Tier runner ───────────────────────────────────────────────────────────────
should_run() {
  local t="$1"
  [[ "$TIER" == "all" || "$TIER" == "$t" ]]
}

# ── Main ──────────────────────────────────────────────────────────────────────
sep
echo -e "${BOLD}  Cenomi Chatbot — Live Test Suite${RESET}"
echo -e "  Tier: ${CYAN}${TIER}${RESET}   No-start: ${CYAN}${NO_START}${RESET}"
sep
echo ""

# ── Tier 1: pytest unit + integration + mocked E2E ───────────────────────────
if should_run "unit"; then
  run_tier \
    "Tier 1 — pytest (unit + integration + mocked E2E)" \
    "source .venv/bin/activate && \
     pytest tests/unit/ tests/integration/ tests/e2e/ \
       -v --tb=short --ignore=tests/eval \
       -q 2>&1 | tee /tmp/tier1_results.txt" \
    "$BACKEND_DIR"
fi

# Start live stack for tiers 2–5
if should_run "eval" || should_run "manual" || should_run "ui"; then
  start_stack
fi

# ── Tier 2: Python eval runner (33 LLM scenarios) ────────────────────────────
if should_run "eval"; then
  EVAL_ARGS=""
  [[ -n "$SCENARIOS_FILTER" ]] && EVAL_ARGS="$EVAL_ARGS --scenarios $SCENARIOS_FILTER"
  [[ -n "$TAGS_FILTER"      ]] && EVAL_ARGS="$EVAL_ARGS --tags $TAGS_FILTER"
  [[ -n "$VERBOSE"          ]] && EVAL_ARGS="$EVAL_ARGS $VERBOSE"

  run_tier \
    "Tier 2 — Agent eval (run_eval.py, 33 scenarios)" \
    "source .venv/bin/activate && \
     python -m tests.eval.run_eval $EVAL_ARGS \
       2>&1 | tee /tmp/tier2_eval_results.txt" \
    "$BACKEND_DIR"
fi

# ── Tier 3: Manual blocks eval ────────────────────────────────────────────────
if should_run "manual"; then
  run_tier \
    "Tier 3 — Manual blocks eval (test_manual_blocks.py)" \
    "source .venv/bin/activate && \
     python -m tests.eval.test_manual_blocks $VERBOSE \
       2>&1 | tee /tmp/tier3_manual_results.txt" \
    "$BACKEND_DIR"
fi

# ── Tier 4: Playwright live UI tests ─────────────────────────────────────────
if should_run "ui"; then
  run_tier \
    "Tier 4 — Playwright live UI (lifecycle-live.spec.ts)" \
    "npx playwright test --config playwright.live.config.ts \
       tests/e2e/live/lifecycle-live.spec.ts \
       2>&1 | tee /tmp/tier4_playwright_results.txt" \
    "$FRONTEND_DIR"
fi

# ── Tier 5: Playwright agent eval ─────────────────────────────────────────────
if should_run "eval" || should_run "all"; then
  GREP_FILTER=""
  [[ -n "$SCENARIOS_FILTER" ]] && {
    # Convert "1,3,6" to playwright --grep "Scenario (1|3|6):"
    GREP_PAT=$(echo "$SCENARIOS_FILTER" | tr ',' '|')
    GREP_FILTER="--grep \"Scenario (${GREP_PAT}):\""
  }

  run_tier \
    "Tier 5 — Playwright agent eval (agent-eval.spec.ts, 33 scenarios)" \
    "npx playwright test --config playwright.live.config.ts \
       tests/e2e/live/agent-eval.spec.ts $GREP_FILTER \
       2>&1 | tee /tmp/tier5_agenteval_results.txt" \
    "$FRONTEND_DIR"
fi

# ── Final summary ─────────────────────────────────────────────────────────────
sep
echo -e "${BOLD}  TEST SUITE SUMMARY${RESET}"
sep
echo ""

for tier_name in \
  "Tier 1 — pytest (unit + integration + mocked E2E)" \
  "Tier 2 — Agent eval (run_eval.py, 33 scenarios)" \
  "Tier 3 — Manual blocks eval (test_manual_blocks.py)" \
  "Tier 4 — Playwright live UI (lifecycle-live.spec.ts)" \
  "Tier 5 — Playwright agent eval (agent-eval.spec.ts, 33 scenarios)"; do

  if [[ -n "${TIER_RESULTS[$tier_name]+x}" ]]; then
    result="${TIER_RESULTS[$tier_name]}"
    elapsed="${TIER_TIMES[$tier_name]}s"
    if [[ "$result" == "PASSED" ]]; then
      echo -e "  ${GREEN}PASSED${RESET}  $tier_name  ${CYAN}(${elapsed})${RESET}"
    else
      echo -e "  ${RED}FAILED${RESET}  $tier_name  ${CYAN}(${elapsed})${RESET}"
    fi
  fi
done

echo ""
sep

if [[ "$OVERALL_EXIT" -eq 0 ]]; then
  echo -e "${GREEN}${BOLD}  ALL TIERS PASSED${RESET}"
else
  echo -e "${RED}${BOLD}  ONE OR MORE TIERS FAILED${RESET}"
fi

echo ""
echo "  Logs:"
ls /tmp/tier*.txt 2>/dev/null | while read -r f; do echo "    $f"; done
if [[ -d "$FRONTEND_DIR/playwright-report-live" ]]; then
  echo "  Playwright HTML report: $FRONTEND_DIR/playwright-report-live/index.html"
fi
sep

exit $OVERALL_EXIT
