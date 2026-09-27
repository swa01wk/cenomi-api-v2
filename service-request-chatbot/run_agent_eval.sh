#!/usr/bin/env bash
# run_agent_eval.sh — Tiered agent evaluation suite
#
# Tier structure:
#   0  Smoke     (~1 min)   S201–S202 + lifecycle FM smoke — fast sanity gate
#   1  Core      (~5 min)   S1–S15    — CREATE_SR happy path & basic flows
#   2  Edge      (~8 min)   S16–S33, S60–S64 — edge + lease eligibility (S63/S64)
#   3  Battle    (~12 min)  S34–S59   — Issues 1–7 from agent_ui_fixes.md
#   4  FM        (~10 min)  L134–L139 — FM_REVIEW lifecycle
#   5  RDD       (~12 min)  L140–L145 — RDD_REVIEW lifecycle
#   6  Full-LC   (~25 min)  L146–L173 — full lifecycle + battle + multi-file (L173)
#   8  FAQ       (~3 min)   S201, S208–S212
#   9  Work Permit (~15 min) S207, S240–S244 + L240–L245 review hops
#   all           All tiers in sequence (equivalent to old a+b+c)
#
# Usage:
#   ./run_agent_eval.sh                            # full suite (all tiers), starts services
#   ./run_agent_eval.sh --no-start                 # services already running
#   ./run_agent_eval.sh --no-start --tier 0        # smoke only (~2 min sanity gate)
#   ./run_agent_eval.sh --no-start --tier 0p       # pipeline smoke (~5 min)
#   ./run_agent_eval.sh --no-start --tier 1        # CREATE_SR happy
#   ./run_agent_eval.sh --no-start --tier 1,2,3    # CREATE_SR full suite
#   ./run_agent_eval.sh --no-start --tier 3a,3b,3c # FM_REVIEW full suite
#   ./run_agent_eval.sh --no-start --tier 3d,3e,3f # RDD_REVIEW full suite
#   ./run_agent_eval.sh --no-start --tier 3g,3h,3i # Full lifecycle full suite
#   ./run_agent_eval.sh --no-start --tier 1,2,3,3a,3b,3c,3d,3e,3f,3g,3h,3i  # all Handover A
#   ./run_agent_eval.sh --no-start --tier 4        # Handover B happy only
#   ./run_agent_eval.sh --no-start --tier 6a,6b,6c,6d  # FOS full suite
#   ./run_agent_eval.sh --no-start --tier 6e,6f,6g     # END full suite
#   ./run_agent_eval.sh --no-start --tier 6h,6i,6j     # TRADING full suite
#   ./run_agent_eval.sh --no-start --tier 6a,6b,6c,6d,6e,6f,6g,6h,6i,6j  # all Fitout per-agent
#   ./run_agent_eval.sh --no-start --tier 6k           # Fitout Progress (create side)
#   ./run_agent_eval.sh --no-start --tier 8        # FAQ + RBAC
#   ./run_agent_eval.sh --no-start --tier 9        # Work Permit create + review hops
#   ./run_agent_eval.sh --no-start --tier sd0      # Sales Data smoke (~2 min)
#   ./run_agent_eval.sh --no-start --tier sd1      # Sales Data happy path
#   ./run_agent_eval.sh --no-start --tier sd2      # Sales Data edge cases
#   ./run_agent_eval.sh --no-start --tier sd3      # Sales Data admin + RBAC
#   ./run_agent_eval.sh --no-start --tier sd       # all Sales Data tiers
#   ./run_agent_eval.sh --no-start --tier all      # all tiers (incl. Work Permit + Sales Data)
#   ./run_agent_eval.sh --no-start --verbose       # verbose turn-by-turn output
#   ./run_agent_eval.sh --no-start --no-obs        # skip observability export
#
# Exit codes:
#   0 — all selected tiers passed
#   1 — one or more tiers failed, or server unreachable

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"

# ── Defaults ──────────────────────────────────────────────────────────────────
BASE_URL="http://localhost:8000"
NO_START=0
VERBOSE=""
NO_OBS=0
TIERS="all"
TAGS=""
BACKEND_PID=""

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-start)   NO_START=1 ;;
    --base-url)   BASE_URL="$2"; shift ;;
    --verbose|-v) VERBOSE="--verbose" ;;
    --no-obs)     NO_OBS=1 ;;
    --tier)       TIERS="$2"; shift ;;
    --scenarios)  # Legacy alias — map old a/b/c to new tier numbers
      case "$2" in
        a)   TIERS="1" ;;
        b)   TIERS="3" ;;
        c)   TIERS="4,5,6a,6b,6c,6d,6e,6f,6g,6h,6i,6j" ;;
        ab)  TIERS="1,2,3" ;;
        abc) TIERS="all" ;;
        *)   TIERS="$2" ;;  # pass-through numeric tier
      esac
      shift ;;
    --tags)       TAGS="$2"; shift ;;
    -h|--help)
      sed -n '/^# Usage/,/^#$/p' "$0" | sed 's/^# //' | sed 's/^#//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Run with --help for usage." >&2
      exit 1
      ;;
  esac
  shift
done

# Expand tier aliases
if [[ "$TIERS" == "all" ]]; then
  TIERS="0,0p,1,2,3,3a,3b,3c,3d,3e,3f,3g,3h,3i,4,5,6a,6b,6c,6d,6e,6f,6g,6h,6i,6j,8,9,sd0,sd1,sd2,sd3"
fi
if [[ "$TIERS" == "sd" ]]; then
  TIERS="sd0,sd1,sd2,sd3"
fi

# ── Results dir ───────────────────────────────────────────────────────────────
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
RESULTS="$SCRIPT_DIR/results/eval_${TIMESTAMP}"
mkdir -p "$RESULTS/obs"

# ── Pretty-print helpers ──────────────────────────────────────────────────────
_div()  { printf '%.0s═' {1..72}; printf '\n'; }
_step() { echo; _div; echo "  $*"; _div; }
_ok()   { echo "  ✓ $*"; }
_warn() { echo "  ⚠ $*"; }
_err()  { echo "  ✗ $*" >&2; }

# ── Health poll ───────────────────────────────────────────────────────────────
_wait_for_health() {
    local url="$BASE_URL/api/v1/health"
    local max=90 elapsed=0
    echo -n "  Waiting for backend to be ready"
    while [[ $elapsed -lt $max ]]; do
        if curl -sf "$url" >/dev/null 2>&1; then
            echo " ✓"
            return 0
        fi
        sleep 3
        elapsed=$((elapsed + 3))
        echo -n "."
    done
    echo
    _err "Backend health-check timed out after ${max}s — $url"
    return 1
}

# ── Metrics snapshot ──────────────────────────────────────────────────────────
_fetch_metrics() {
    curl -sf "$BASE_URL/api/observability/metrics/summary" 2>/dev/null \
        || printf '{"error":"metrics unavailable"}'
}

# ── EXIT cleanup ─────────────────────────────────────────────────────────────
_cleanup() {
    local rc=$?
    if [[ -n "$BACKEND_PID" ]]; then
        echo
        echo "  Stopping backend (PID $BACKEND_PID)..."
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi
    if [[ -d "$RESULTS" ]]; then
        echo
        _div
        echo "  Results folder: $RESULTS"
        _div
    fi
    exit "$rc"
}
trap _cleanup EXIT INT TERM

# ─────────────────────────────────────────────────────────────────────────────
_step "AGENT EVAL SUITE  —  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Results : $RESULTS"
echo "  Base URL: $BASE_URL"
echo "  Tiers   : $TIERS"
[[ -n "$VERBOSE" ]] && echo "  Mode    : verbose"
[[ $NO_OBS -eq 1 ]] && echo "  Obs     : skipped (--no-obs)"

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — START SERVICES
# ─────────────────────────────────────────────────────────────────────────────
if [[ $NO_START -eq 0 ]]; then
    _step "PHASE 1 — START SERVICES"

    echo "  Starting postgres + redis via docker compose..."
    (cd "$SCRIPT_DIR" && docker compose up -d postgres redis)

    echo "  Running alembic migrations..."
    (cd "$BACKEND_DIR" && alembic upgrade head)

    echo "  Seeding users..."
    (cd "$BACKEND_DIR" && python3 scripts/seed_users.py)

    echo "  Starting uvicorn backend..."
    (cd "$BACKEND_DIR" && uvicorn app.main:app \
        --host 0.0.0.0 \
        --port 8000 \
        > >(tee "$RESULTS/backend.log") 2>&1) &
    BACKEND_PID=$!

    _wait_for_health
else
    _step "PHASE 1 — START SERVICES  (skipped — --no-start)"
    echo "  Using existing backend at $BASE_URL"
    if ! curl -sf "$BASE_URL/api/v1/health" >/dev/null 2>&1; then
        _err "Backend unreachable at $BASE_URL/api/v1/health"
        exit 1
    fi
    _ok "Backend reachable"
fi

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — METRICS BASELINE
# ─────────────────────────────────────────────────────────────────────────────
_step "PHASE 2 — METRICS BASELINE"
_fetch_metrics > "$RESULTS/metrics_baseline.json"
_ok "Baseline captured → metrics_baseline.json"

# ─────────────────────────────────────────────────────────────────────────────
# Helper: run one eval tier and capture exit code
# ─────────────────────────────────────────────────────────────────────────────
# Tier exit-code tracking — uses temp files so any tier name (including "0p")
# works on all bash versions (bash 3.x on macOS lacks associative array support).
TIER_EXIT_DIR="$RESULTS/.tier_exits"
mkdir -p "$TIER_EXIT_DIR"

_tier_set_exit() { echo "${2}" > "$TIER_EXIT_DIR/${1}"; }
_tier_get_exit() { cat "$TIER_EXIT_DIR/${1}" 2>/dev/null || echo 0; }

_run_scenarios_tier() {
    local tier="$1"
    local label="$2"
    local log_name="$3"

    _step "TIER ${tier} — ${label}"

    local log_file="$RESULTS/${log_name}.log"
    local json_file="$RESULTS/${log_name}.json"

    local args=(
        --base-url "$BASE_URL"
        --tier "$tier"
        --json "$json_file"
    )
    [[ -n "$VERBOSE" ]] && args+=("$VERBOSE")
    [[ -n "$TAGS"    ]] && args+=(--tags "$TAGS")

    (cd "$BACKEND_DIR" && PYTHONUNBUFFERED=1 python3 -u -m tests.eval.run_eval "${args[@]}") \
        2>&1 | tee "$log_file"
    local exit_code="${PIPESTATUS[0]}"
    _tier_set_exit "$tier" "$exit_code"

    if [[ $exit_code -eq 0 ]]; then
        _ok "Tier ${tier} PASSED"
    else
        _warn "Tier ${tier} FAILED (exit ${exit_code}) — continuing to next tier"
    fi
}

_run_lifecycle_tier() {
    local tier="$1"
    local label="$2"
    local log_name="$3"

    _step "LIFECYCLE TIER ${tier} — ${label}"

    local log_file="$RESULTS/${log_name}.log"
    local json_file="$RESULTS/${log_name}.json"

    local args=(
        --base-url "$BASE_URL"
        --tier "$tier"
        --json "$json_file"
    )
    [[ -n "$VERBOSE" ]] && args+=("$VERBOSE")
    [[ -n "$TAGS"    ]] && args+=(--tags "$TAGS")

    (cd "$BACKEND_DIR" && PYTHONUNBUFFERED=1 python3 -u -m tests.eval.run_lifecycle_eval "${args[@]}") \
        2>&1 | tee "$log_file"
    local exit_code="${PIPESTATUS[0]}"
    _tier_set_exit "$tier" "$exit_code"

    if [[ $exit_code -eq 0 ]]; then
        _ok "Lifecycle tier ${tier} PASSED"
    else
        _warn "Lifecycle tier ${tier} FAILED (exit ${exit_code}) — continuing"
    fi
}

_run_sales_data_tier() {
    local tier="$1"
    local label="$2"
    local log_name="$3"

    _step "SALES DATA TIER ${tier} — ${label}"

    local log_file="$RESULTS/${log_name}.log"
    local json_file="$RESULTS/${log_name}.json"

    local args=(
        --base-url "$BASE_URL"
        --tier "$tier"
        --json "$json_file"
    )
    [[ -n "$VERBOSE" ]] && args+=("$VERBOSE")

    (cd "$BACKEND_DIR" && PYTHONUNBUFFERED=1 python3 -u -m tests.eval.run_sales_data_eval "${args[@]}") \
        2>&1 | tee "$log_file"
    local exit_code="${PIPESTATUS[0]}"
    _tier_set_exit "$tier" "$exit_code"

    if [[ $exit_code -eq 0 ]]; then
        _ok "Sales Data tier ${tier} PASSED"
    else
        _warn "Sales Data tier ${tier} FAILED (exit ${exit_code}) — continuing"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# EXECUTE TIERS
# ─────────────────────────────────────────────────────────────────────────────
IFS=',' read -ra TIER_LIST <<< "$TIERS"

for tier in "${TIER_LIST[@]}"; do
  case "$tier" in
    0)
      _run_scenarios_tier 0 "Smoke-Agent — per-agent sanity (S200–S201, S203–S206)" "tier_0_smoke"
      # Also run lifecycle smoke if tier 0 is selected
      _run_lifecycle_tier 0 "Smoke — FM approve lifecycle" "tier_0_smoke_fm"
      ;;
    0p)
      _run_lifecycle_tier 0p "Smoke-Pipeline — Handover A 3-phase + FOS create→approve (L298–L299)" "tier_0p_pipeline_smoke"
      ;;
    1)
      _run_scenarios_tier 1 "Handover A Happy — CREATE_SR core (S1–S15, S63–S64)" "tier_1_handover_a_happy"
      ;;
    2)
      _run_scenarios_tier 2 "Handover A Edge — corrections, date violations (S16–S33, S60–S68)" "tier_2_handover_a_edge"
      ;;
    3)
      _run_scenarios_tier 3 "CREATE_SR Battle — Issues 1–7 agent-fix regressions (S34–S59)" "tier_3_battle"
      ;;
    3a)
      _run_lifecycle_tier "3a" "FM_REVIEW Happy — date+approve, save-progress (L134–L135)" "tier_3a_fm_happy"
      ;;
    3b)
      _run_lifecycle_tier "3b" "FM_REVIEW Edge — NL date, multi-field, Operations role (L136–L137, L139)" "tier_3b_fm_edge"
      ;;
    3c)
      _run_lifecycle_tier "3c" "FM_REVIEW Battle+RBAC — DD_ENGINEER denied (L138)" "tier_3c_fm_battle"
      ;;
    3d)
      _run_lifecycle_tier "3d" "RDD_REVIEW Happy — packed + turn-by-turn (L140–L141)" "tier_3d_rdd_happy"
      ;;
    3e)
      _run_lifecycle_tier "3e" "RDD_REVIEW Edge — date-chain, APPROVED_WITH_CHANGES, NL dates (L142–L144)" "tier_3e_rdd_edge"
      ;;
    3f)
      _run_lifecycle_tier "3f" "RDD_REVIEW Battle+RBAC — FM_MANAGER denied (L145)" "tier_3f_rdd_battle"
      ;;
    3g)
      _run_lifecycle_tier "3g" "Full Lifecycle Happy — CREATE→FM→RDD→SR_COMPLETED (L146)" "tier_3g_lc_happy"
      ;;
    3h)
      _run_lifecycle_tier "3h" "Full Lifecycle Edge — corrections + MM upload gate (L147–L149)" "tier_3h_lc_edge"
      ;;
    3i)
      _run_lifecycle_tier "3i" "Full Lifecycle Battle — state isolation, persistence, contracts (L150–L172)" "tier_3i_lc_battle"
      ;;
    4)
      _run_lifecycle_tier 4 "Handover B Happy — already_trading packed + gradual (L150–L151)" "tier_4_handover_b_happy"
      ;;
    5)
      _run_lifecycle_tier 5 "Handover B Edge+Battle — date ordering, path switch, RBAC (L152–L159)" "tier_5_handover_b_edge"
      ;;
    # ── Fitout Start (FOS) — per-agent sub-tiers ──────────────────────────────
    6a)
      _run_scenarios_tier "6a" "FOS Happy flat — yes-branch create (S73)" "tier_6a_fos_happy"
      _run_lifecycle_tier "6a" "FOS Happy lifecycle — single-turn create (L170)" "tier_6a_fos_happy_lc"
      ;;
    6b)
      _run_scenarios_tier "6b" "FOS Edge flat — intent, no-branch, date-order, title (S70, S75, S78, S82)" "tier_6b_fos_edge"
      _run_lifecycle_tier "6b" "FOS Edge lifecycle — no-branch with delay_reason (L171)" "tier_6b_fos_edge_lc"
      ;;
    6c)
      _run_scenarios_tier "6c" "FOS Battle+RBAC flat — payload, DD_ENGINEER approve (S79, S81)" "tier_6c_fos_battle"
      _run_lifecycle_tier "6c" "FOS Battle+RBAC lifecycle — cancel, RBAC, stale state (L179, L181, L182)" "tier_6c_fos_battle_lc"
      ;;
    6d)
      _run_lifecycle_tier "6d" "FOS Lifecycle — APPROVE_SR: open existing FOS SR → SR_APPROVED (L172)" "tier_6d_fos_lifecycle"
      ;;
    # ── Fitout End (END) — per-agent sub-tiers ────────────────────────────────
    6e)
      _run_scenarios_tier "6e" "END Happy flat — yes-branch create (S83)" "tier_6e_end_happy"
      _run_lifecycle_tier "6e" "END Happy lifecycle — packed create (L173)" "tier_6e_end_happy_lc"
      ;;
    6f)
      _run_scenarios_tier "6f" "END Edge flat — intent, no-branch planned dates (S71, S77)" "tier_6f_end_edge"
      _run_lifecycle_tier "6f" "END Edge lifecycle — no-branch with delay_reason (L174)" "tier_6f_end_edge_lc"
      ;;
    6g)
      _run_lifecycle_tier "6g" "END Lifecycle — APPROVE_SR: open existing END SR → SR_APPROVED (L175)" "tier_6g_end_lifecycle"
      ;;
    # ── Trading Confirmation (TRADING) — per-agent sub-tiers ─────────────────
    6h)
      _run_scenarios_tier "6h" "TRADING Happy flat — yes-branch create (S74)" "tier_6h_trading_happy"
      _run_lifecycle_tier "6h" "TRADING Happy lifecycle — packed create (L176)" "tier_6h_trading_happy_lc"
      ;;
    6i)
      _run_scenarios_tier "6i" "TRADING Edge flat — intent, no-branch delay optional (S72, S76)" "tier_6i_trading_edge"
      _run_lifecycle_tier "6i" "TRADING Edge lifecycle — no-branch no delay_reason (L177)" "tier_6i_trading_edge_lc"
      ;;
    6j)
      _run_scenarios_tier "6j" "TRADING Battle+RBAC flat — MALL_MANAGER create (S80)" "tier_6j_trading_battle"
      _run_lifecycle_tier "6j" "TRADING Battle+RBAC lifecycle — approve, DD_ENGINEER denied (L178, L180)" "tier_6j_trading_battle_lc"
      ;;
    8)
      _run_scenarios_tier 8 "FAQ + RBAC — help queries, status, RBAC denials (S201, S208–S212)" "tier_8_faq_rbac"
      ;;
    9)
      _run_scenarios_tier 9 "Work Permit create — intent, packed create, RBAC (S240–S244)" "tier_9_wp_create"
      _run_lifecycle_tier 9 "Work Permit review hops — Hot/Cold/Maint/Ops + reject + RBAC (L240–L245)" "tier_9_wp_review"
      ;;
    notif)
      _run_scenarios_tier notif "Notification agent — inbox listing, filters, routing (S250–S255)" "tier_9_notifications"
      ;;
    # ── Sales Data Agent tiers ────────────────────────────────────────────────
    sd0)
      _run_sales_data_tier "sd0" "Sales Data Smoke — upload intent + RBAC (SD200–SD201)" "tier_sd0_smoke"
      ;;
    sd1)
      _run_sales_data_tier "sd1" "Sales Data Happy — manual entry, status, admin review (SD210–SD212)" "tier_sd1_happy"
      ;;
    sd2)
      _run_sales_data_tier "sd2" "Sales Data Edge — future date, zero amt, cancel, missing fields (SD220–SD223)" "tier_sd2_edge"
      ;;
    sd3)
      _run_sales_data_tier "sd3" "Sales Data Admin RBAC — tenant-approve denied, admin-upload denied (SD230–SD231)" "tier_sd3_rbac"
      ;;
    *)
      _warn "Unknown tier '${tier}' — skipped"
      ;;
  esac
done

# ─────────────────────────────────────────────────────────────────────────────
# METRICS FINAL + OBSERVABILITY EXPORT
# ─────────────────────────────────────────────────────────────────────────────
_step "METRICS FINAL + OBSERVABILITY EXPORT"

_fetch_metrics > "$RESULTS/metrics_final.json"
_ok "Final metrics captured → metrics_final.json"

if [[ $NO_OBS -eq 0 ]]; then
    echo "  Exporting session observability traces (last 120 minutes)..."
    (cd "$BACKEND_DIR" && python3 scripts/export_session_observability.py \
        --recent 120 \
        --eval \
        --out "$RESULTS/obs/" \
        --base-url "$BASE_URL") \
        2>&1 | tee "$RESULTS/obs_export.log" || true
    _ok "Observability export complete → $RESULTS/obs/"
else
    _ok "Observability export skipped (--no-obs)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# REPORT.md — aggregate results across all executed tiers
# ─────────────────────────────────────────────────────────────────────────────
_step "REPORT.md"

python3 - "$RESULTS" "$TIMESTAMP" "$BASE_URL" "$TIERS" <<'PYEOF'
from __future__ import annotations
import json, sys, glob
from pathlib import Path

results_dir = Path(sys.argv[1])
timestamp   = sys.argv[2]
base_url    = sys.argv[3]
tiers_run   = sys.argv[4]


def load_json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def load_ndjson(p: Path):
    rows = []
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return rows


def _tier_stats(data: list) -> dict:
    if not data:
        return {"scenarios": 0, "passed": 0, "turns_passed": 0, "turns_total": 0, "slow_turns": 0}
    passed_scenarios = sum(1 for s in data if s.get("passed"))
    return {
        "scenarios":    len(data),
        "passed":       passed_scenarios,
        "turns_passed": sum(s.get("turns_passed", 0) for s in data),
        "turns_total":  sum(s.get("turns_total",  0) for s in data),
        "slow_turns":   sum(
            1 for s in data for t in s.get("turns", [])
            if (t.get("latency_ms") or 0) > 15_000
        ),
    }


def _lifecycle_tier_stats(data: list) -> dict:
    if not data:
        return {"scenarios": 0, "passed": 0, "phases_passed": 0, "turns_passed": 0, "turns_total": 0}
    passed_scenarios = sum(1 for s in data if s.get("passed"))
    return {
        "scenarios":     len(data),
        "passed":        passed_scenarios,
        "phases_passed": sum(
            sum(1 for p in s.get("phases", []) if p.get("passed"))
            for s in data
        ),
        "turns_passed":  sum(s.get("turns_passed",  0) for s in data),
        "turns_total":   sum(s.get("turns_expected", s.get("turns_total", 0)) for s in data),
    }


def pct(n, d):
    return f"{100*n/d:.1f}%" if d > 0 else "N/A"


# Load available tier JSON files
tier_data = {}
for f in results_dir.glob("tier_*.json"):
    try:
        data = json.loads(f.read_text())
        tier_data[f.stem] = data if isinstance(data, list) else []
    except Exception:
        pass

# Metrics delta
baseline = load_json(results_dir / "metrics_baseline.json")
final    = load_json(results_dir / "metrics_final.json")
manifest = load_ndjson(results_dir / "obs" / "manifest.ndjson")

def _int(d, k):   return int(d.get(k) or 0)
def _float(d, k): return float(d.get(k) or 0.0)

new_traces  = _int(final, "total_traces")  - _int(baseline, "total_traces")
new_failed  = _int(final, "failed_traces") - _int(baseline, "failed_traces")
tokens      = _int(final, "total_tokens")  - _int(baseline, "total_tokens")
cost_usd    = round(_float(final, "total_cost") - _float(baseline, "total_cost"), 6)
eval_sr     = round((new_traces - new_failed) / new_traces, 4) if new_traces > 0 else 0.0
latencies   = [m.get("latency_ms") or 0 for m in manifest]
avg_latency = int(sum(latencies) / len(latencies)) if latencies else 0

# Build per-tier rows
TIER_INFO = [
    ("0",    "Smoke-Agent",              "tier_0_smoke",              False),
    ("0fm",  "Smoke FM lifecycle",       "tier_0_smoke_fm",           True),
    ("0p",   "Smoke-Pipeline",           "tier_0p_pipeline_smoke",    True),
    # Handover A — Stage 1: CREATE_SR
    ("1",    "CREATE_SR Happy",          "tier_1_handover_a_happy",   False),
    ("2",    "CREATE_SR Edge",           "tier_2_handover_a_edge",    False),
    ("3",    "CREATE_SR Battle",         "tier_3_battle",             False),
    # Handover A — Stage 2: FM_REVIEW
    ("3a",   "FM_REVIEW Happy",          "tier_3a_fm_happy",          True),
    ("3b",   "FM_REVIEW Edge",           "tier_3b_fm_edge",           True),
    ("3c",   "FM_REVIEW Battle+RBAC",    "tier_3c_fm_battle",         True),
    # Handover A — Stage 3: RDD_REVIEW
    ("3d",   "RDD_REVIEW Happy",         "tier_3d_rdd_happy",         True),
    ("3e",   "RDD_REVIEW Edge",          "tier_3e_rdd_edge",          True),
    ("3f",   "RDD_REVIEW Battle+RBAC",   "tier_3f_rdd_battle",        True),
    # Handover A — Full Lifecycle
    ("3g",   "Full LC Happy",            "tier_3g_lc_happy",          True),
    ("3h",   "Full LC Edge",             "tier_3h_lc_edge",           True),
    ("3i",   "Full LC Battle",           "tier_3i_lc_battle",         True),
    # Handover B
    ("4",    "Handover B Happy",         "tier_4_handover_b_happy",   True),
    ("5",    "Handover B Edge+Battle",   "tier_5_handover_b_edge",    True),
    # Fitout Start (FOS) per-agent
    ("6a",   "FOS Happy flat",           "tier_6a_fos_happy",         False),
    ("6alc", "FOS Happy LC",             "tier_6a_fos_happy_lc",      True),
    ("6b",   "FOS Edge flat",            "tier_6b_fos_edge",          False),
    ("6blc", "FOS Edge LC",              "tier_6b_fos_edge_lc",       True),
    ("6c",   "FOS Battle+RBAC flat",     "tier_6c_fos_battle",        False),
    ("6clc", "FOS Battle+RBAC LC",       "tier_6c_fos_battle_lc",     True),
    ("6d",   "FOS Lifecycle",            "tier_6d_fos_lifecycle",      True),
    # Fitout End (END) per-agent
    ("6e",   "END Happy flat",           "tier_6e_end_happy",         False),
    ("6elc", "END Happy LC",             "tier_6e_end_happy_lc",      True),
    ("6f",   "END Edge flat",            "tier_6f_end_edge",          False),
    ("6flc", "END Edge LC",              "tier_6f_end_edge_lc",       True),
    ("6g",   "END Lifecycle",            "tier_6g_end_lifecycle",      True),
    # Trading Confirmation (TRADING) per-agent
    ("6h",   "TRADING Happy flat",       "tier_6h_trading_happy",     False),
    ("6hlc", "TRADING Happy LC",         "tier_6h_trading_happy_lc",  True),
    ("6i",   "TRADING Edge flat",        "tier_6i_trading_edge",      False),
    ("6ilc", "TRADING Edge LC",          "tier_6i_trading_edge_lc",   True),
    ("6j",   "TRADING Battle+RBAC flat", "tier_6j_trading_battle",    False),
    ("6jlc", "TRADING Battle+RBAC LC",   "tier_6j_trading_battle_lc", True),
    # FAQ + RBAC
    ("8",    "FAQ + RBAC",               "tier_8_faq_rbac",           False),
    ("9",    "WP create flat",           "tier_9_wp_create",          False),
    ("9lc",  "WP review hops",           "tier_9_wp_review",          True),
    # ── Sales Data Agent ──────────────────────────────────────────────────────
    ("sd0",  "Sales Data Smoke",         "tier_sd0_smoke",            False),
    ("sd1",  "Sales Data Happy",         "tier_sd1_happy",            False),
    ("sd2",  "Sales Data Edge",          "tier_sd2_edge",             False),
    ("sd3",  "Sales Data Admin RBAC",    "tier_sd3_rbac",             False),
]

lines = [
    "# Agent Eval Report",
    "",
    f"**Run:** `{timestamp}`  |  **Base URL:** `{base_url}`  |  **Tiers:** `{tiers_run}`",
    "",
    "---",
    "",
    "## Metrics Dashboard",
    "",
    "| Metric | Value |",
    "|--------|-------|",
    f"| New traces | {new_traces} |",
    f"| New failures | {new_failed} |",
    f"| Eval success rate | {pct(new_traces - new_failed, new_traces)} |",
    f"| Avg latency (ms) | {avg_latency} |",
    f"| Tokens consumed | {tokens:,} |",
    f"| Cost (USD) | ${cost_usd:.4f} |",
    "",
    "---",
    "",
    "## Per-Tier Results",
    "",
    "| Tier | Description | Scenarios | Passed | Turns Passed | Turns Total | Pass % | Slow >15s |",
    "|------|-------------|-----------|--------|--------------|-------------|--------|-----------|",
]

all_failed_scenarios = []
for tier_id, desc, stem, is_lifecycle in TIER_INFO:
    data = tier_data.get(stem, [])
    if not data:
        continue  # tier was not executed — omit row rather than show dashes
    if is_lifecycle:
        s = _lifecycle_tier_stats(data)
        slow = sum(
            1 for sc in data for ph in sc.get("phases", [])
            for t in ph.get("turns", []) if (t.get("latency_ms") or 0) > 15_000
        )
        lines.append(
            f"| {tier_id} | {desc} | {s['scenarios']} | {s['passed']} | "
            f"{s['turns_passed']} | {s['turns_total']} | "
            f"{pct(s['turns_passed'], s['turns_total'])} | {slow} |"
        )
    else:
        s = _tier_stats(data)
        lines.append(
            f"| {tier_id} | {desc} | {s['scenarios']} | {s['passed']} | "
            f"{s['turns_passed']} | {s['turns_total']} | "
            f"{pct(s['turns_passed'], s['turns_total'])} | {s['slow_turns']} |"
        )
    for sc in data:
        if not sc.get("passed"):
            all_failed_scenarios.append((tier_id, sc))

# ── Per-scenario log extraction ───────────────────────────────────────────────
import re as _re

logs_dir = results_dir / "logs"
logs_dir.mkdir(exist_ok=True)

_STEM_MAP = {
    "0":    "tier_0_smoke",
    "0fm":  "tier_0_smoke_fm",
    "0p":   "tier_0p_pipeline_smoke",
    "1":    "tier_1_handover_a_happy",
    "2":    "tier_2_handover_a_edge",
    "3":    "tier_3_battle",
    "3a":   "tier_3a_fm_happy",
    "3b":   "tier_3b_fm_edge",
    "3c":   "tier_3c_fm_battle",
    "3d":   "tier_3d_rdd_happy",
    "3e":   "tier_3e_rdd_edge",
    "3f":   "tier_3f_rdd_battle",
    "3g":   "tier_3g_lc_happy",
    "3h":   "tier_3h_lc_edge",
    "3i":   "tier_3i_lc_battle",
    "4":    "tier_4_handover_b_happy",
    "5":    "tier_5_handover_b_edge",
    "6a":   "tier_6a_fos_happy",
    "6alc": "tier_6a_fos_happy_lc",
    "6b":   "tier_6b_fos_edge",
    "6blc": "tier_6b_fos_edge_lc",
    "6c":   "tier_6c_fos_battle",
    "6clc": "tier_6c_fos_battle_lc",
    "6d":   "tier_6d_fos_lifecycle",
    "6e":   "tier_6e_end_happy",
    "6elc": "tier_6e_end_happy_lc",
    "6f":   "tier_6f_end_edge",
    "6flc": "tier_6f_end_edge_lc",
    "6g":   "tier_6g_end_lifecycle",
    "6h":   "tier_6h_trading_happy",
    "6hlc": "tier_6h_trading_happy_lc",
    "6i":   "tier_6i_trading_edge",
    "6ilc": "tier_6i_trading_edge_lc",
    "6j":   "tier_6j_trading_battle",
    "6jlc": "tier_6j_trading_battle_lc",
    "8":    "tier_8_faq_rbac",
    "9":    "tier_9_wp_create",
    "9lc":  "tier_9_wp_review",
    # Sales Data Agent
    "sd0":  "tier_sd0_smoke",
    "sd1":  "tier_sd1_happy",
    "sd2":  "tier_sd2_edge",
    "sd3":  "tier_sd3_rbac",
}

# Cache parsed tier log lines keyed by stem
_tier_log_lines: dict[str, list[str]] = {}

def _get_scenario_log(tier_id: str, scenario_id: int, is_lifecycle: bool) -> str | None:
    """Extract the block for this scenario from the tier log and write logs/S<N>_<stem>.log.
    Returns relative path to the written file, or None if not found."""
    stem = _STEM_MAP.get(tier_id)
    if not stem:
        return None
    if stem not in _tier_log_lines:
        log_path = results_dir / f"{stem}.log"
        if not log_path.exists():
            return None
        _tier_log_lines[stem] = log_path.read_text(errors="replace").splitlines()
    raw = _tier_log_lines[stem]

    # Locate the start line for this scenario.
    # Lifecycle logs:  ▶ S<N> ...
    # Flat eval logs:  ▶ Scenario <N>: ...
    if is_lifecycle:
        start_pat = _re.compile(rf"^▶ S{scenario_id}\b")
    else:
        start_pat = _re.compile(rf"^▶ Scenario {scenario_id}[:\s]")
    next_pat  = _re.compile(r"^▶ ")

    start_idx = next((i for i, ln in enumerate(raw) if start_pat.match(ln)), None)
    if start_idx is None:
        return None
    end_idx = next(
        (i for i, ln in enumerate(raw) if i > start_idx and next_pat.match(ln)),
        len(raw),
    )
    snippet = raw[start_idx:end_idx]

    out_file = logs_dir / f"S{scenario_id}_{stem}.log"
    out_file.write_text("\n".join(snippet))
    return f"logs/S{scenario_id}_{stem}.log"


# ── Per-scenario combined trajectory ─────────────────────────────────────────

def _get_scenario_trajectory(s: dict, is_lifecycle: bool) -> str | None:
    """For flat scenarios: link to obs/<session_id>.md.
    For lifecycle scenarios: concatenate all phase obs files into obs/S<N>_combined.md."""
    obs_dir = results_dir / "obs"
    sid = s["scenario_id"]

    if not is_lifecycle:
        session_id = s.get("session_id")
        if not session_id:
            return None
        md = obs_dir / f"{session_id}.md"
        return f"obs/{session_id}.md" if md.exists() else None

    # Lifecycle — collect all phase session IDs in phase order
    phase_session_ids = [
        ph.get("session_id")
        for ph in s.get("phases", [])
        if ph.get("session_id")
    ]
    if not phase_session_ids:
        return None

    combined_path = obs_dir / f"S{sid}_combined.md"
    parts: list[str] = [
        f"# Scenario S{sid} — Combined Observability Trajectory\n",
        f"**Scenario:** {s['scenario_name']}  ",
        f"**Phases:** {len(s.get('phases', []))}  ",
        "",
    ]
    for ph in s.get("phases", []):
        phase_sid = ph.get("session_id")
        ph_label  = f"Phase {ph['phase_idx']} — {ph.get('stage', '')} ({ph.get('role', '')})"
        parts.append(f"\n---\n\n## {ph_label}\n")
        if phase_sid:
            obs_md = obs_dir / f"{phase_sid}.md"
            if obs_md.exists():
                # Strip the top-level H1 from each obs file to avoid duplicate headers
                body = obs_md.read_text()
                body_lines = body.splitlines()
                # Drop the first line if it's an H1
                if body_lines and body_lines[0].startswith("# "):
                    body = "\n".join(body_lines[1:]).lstrip("\n")
                parts.append(body)
            else:
                parts.append(f"_Trajectory not found for session `{phase_sid}`_\n")
        else:
            parts.append("_No session captured for this phase_\n")

    combined_path.write_text("\n".join(parts))
    return f"obs/S{sid}_combined.md"


# Failing scenarios
lines += [
    "",
    "---",
    "",
    "## Failing Scenarios",
    "",
]
if all_failed_scenarios:
    lines += [
        "| Tier | Scenario | Name | Turn | Assertion | Trajectory | Logs |",
        "|------|----------|------|------|-----------|-----------|------|",
    ]
    for tier_id, s in all_failed_scenarios:
        # Find first failed turn (handles both flat and phase-nested formats)
        ft = None
        is_lifecycle = "phases" in s
        if is_lifecycle:
            for ph in s.get("phases", []):
                ft = next((t for t in ph.get("turns", []) if not t.get("passed")), None)
                if ft:
                    break
        else:
            ft = next((t for t in s.get("turns", []) if not t.get("passed")), None)
        if ft:
            msg = (ft.get("failures") or ["?"])[0].replace("|", "\\|")[:80]
            # Per-scenario trajectory link
            traj_rel = _get_scenario_trajectory(s, is_lifecycle)
            traj_cell = f"[trajectory]({traj_rel})" if traj_rel else "—"
            # Per-scenario log snippet
            log_rel = _get_scenario_log(tier_id, s["scenario_id"], is_lifecycle)
            log_cell = f"[log]({log_rel})" if log_rel else "—"
            lines.append(
                f"| {tier_id} | S{s['scenario_id']} | {s['scenario_name']} | T{ft['turn']} | {msg} | {traj_cell} | {log_cell} |"
            )
else:
    lines.append("_No failures detected._ ✅")

# Slow turns
all_slow = []
for _, s in [(t, sc) for t, sc in [(tid, s) for tid, s in all_failed_scenarios] + []]:
    pass  # already handled above

lines += [
    "",
    "---",
    "",
    "## LLM Usage",
    "",
    "| Metric | Value |",
    "|--------|-------|",
    f"| Total tokens | {tokens:,} |",
    f"| Total cost (USD) | ${cost_usd:.4f} |",
    f"| Avg latency (ms) | {avg_latency} |",
]

# Observability links
obs_dir = results_dir / "obs"
md_files = sorted(obs_dir.glob("*.md")) if obs_dir.exists() else []
if md_files:
    lines += ["", "---", "", "## Observability Trajectories", ""]
    for f in md_files:
        lines.append(f"- [{f.stem}](obs/{f.name})")

lines += [
    "",
    "---",
    "",
    f"_Generated by `run_agent_eval.sh` — {timestamp}_",
    "",
]

(results_dir / "REPORT.md").write_text("\n".join(lines))
print(f"  REPORT.md written → {results_dir / 'REPORT.md'}")
PYEOF

# ─────────────────────────────────────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
echo
_div

ALL_EXIT=0
for f in "$TIER_EXIT_DIR"/*; do
    [[ -f "$f" ]] || continue
    tier="$(basename "$f")"
    ec="$(cat "$f")"
    if [[ "$ec" -ne 0 ]]; then
        ALL_EXIT=1
        _warn "Tier ${tier} FAILED (exit ${ec})"
    fi
done

if [[ $ALL_EXIT -eq 0 ]]; then
    echo "  ✓ ALL TIERS PASSED"
fi

echo "  REPORT: $RESULTS/REPORT.md"
_div

exit "$ALL_EXIT"
