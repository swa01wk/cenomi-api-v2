#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# RDD Lifecycle Test Runner
# Runs the 68 priority tests covering the full RDD + Help Agent lifecycle.
# All external dependencies (LLM, Platform API, DB) are mocked.
# No live server required.
#
# Usage:
#   ./run_lifecycle_tests.sh              # run all 68 tests
#   ./run_lifecycle_tests.sh --coverage   # run with HTML coverage report
#   ./run_lifecycle_tests.sh --fast       # stop on first failure
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Parse flags ──────────────────────────────────────────────────────────────
COVERAGE=false
FAST=false

for arg in "$@"; do
  case $arg in
    --coverage) COVERAGE=true ;;
    --fast)     FAST=true ;;
    --help|-h)
      echo "Usage: $0 [--coverage] [--fast]"
      echo "  --coverage  Generate HTML coverage report in htmlcov/"
      echo "  --fast      Stop on first test failure (-x)"
      exit 0
      ;;
  esac
done

# ── Header ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${CYAN}║       RDD Handover Lifecycle — Priority Test Suite       ║${RESET}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${YELLOW}Scope:${RESET} 68 tests — E2E lifecycle + RBAC + FM/RDD pipeline"
echo -e "  ${YELLOW}Mode:${RESET}  Fully mocked (no live server, LLM, or platform API)"
echo ""

# ── Test targets ─────────────────────────────────────────────────────────────
TESTS=(
  "tests/e2e/test_handover_full_lifecycle_http.py"
  "tests/e2e/test_handover_sr_e2e.py"
  "tests/integration/test_fm_review_e2e.py"
  "tests/integration/test_rdd_review_e2e.py"
  "tests/integration/test_role_stage_access.py"
)

# ── Build pytest args ─────────────────────────────────────────────────────────
PYTEST_ARGS=(
  "${TESTS[@]}"
  "--tb=short"
  "-v"
)

if $FAST; then
  PYTEST_ARGS+=("-x")
  echo -e "  ${YELLOW}Mode:${RESET}  --fast enabled (stop on first failure)"
fi

if $COVERAGE; then
  PYTEST_ARGS+=(
    "--cov=app"
    "--cov-report=term-missing"
    "--cov-report=html:htmlcov"
  )
  echo -e "  ${YELLOW}Coverage:${RESET} HTML report → htmlcov/index.html"
fi

echo ""
echo -e "${BOLD}Running tests...${RESET}"
echo "────────────────────────────────────────────────────────────"
echo ""

# ── Run ───────────────────────────────────────────────────────────────────────
START_TIME=$(date +%s)

if python -m pytest "${PYTEST_ARGS[@]}"; then
  END_TIME=$(date +%s)
  ELAPSED=$((END_TIME - START_TIME))
  echo ""
  echo "────────────────────────────────────────────────────────────"
  echo -e "${GREEN}${BOLD}✓ All lifecycle tests passed${RESET} in ${ELAPSED}s"
  echo ""

  if $COVERAGE; then
    echo -e "  Coverage report: ${CYAN}htmlcov/index.html${RESET}"
    echo ""
  fi

  echo -e "  ${BOLD}Test breakdown:${RESET}"
  echo -e "  • Full 9-step lifecycle HTTP       → 7 tests"
  echo -e "  • CREATE_SR edge cases             → 10 tests"
  echo -e "  • FM_REVIEW pipeline               → 14 tests"
  echo -e "  • RDD_REVIEW Phase 3a + 3b         → 16 tests"
  echo -e "  • RBAC role × stage matrix         → 21 tests"
  echo -e "  ${BOLD}Total: 68 tests${RESET}"
  echo ""
  exit 0
else
  END_TIME=$(date +%s)
  ELAPSED=$((END_TIME - START_TIME))
  echo ""
  echo "────────────────────────────────────────────────────────────"
  echo -e "${RED}${BOLD}✗ Some tests failed${RESET} (${ELAPSED}s)"
  echo ""
  echo -e "  Run with ${CYAN}--fast${RESET} to stop on first failure and see full traceback."
  echo ""
  exit 1
fi
