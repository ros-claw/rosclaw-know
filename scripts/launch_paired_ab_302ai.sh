#!/usr/bin/env bash
# launch_paired_ab_302ai.sh — orchestrate_iteration.sh's sibling for VERIFY.
#
# Project's verify_frontier_eng.py reads DEEPSEEK_API_KEY/BASE_URL from
# os.environ at call time. The repo's .env (gitignored) is the canonical
# place for the 302.ai / z.ai keys; this launcher sources it when present.
# See scripts/orchestrate_iteration.sh for the judge-side equivalent.
#
# Model: step-3.7-flash via 302.ai (free tier on the configured key).
# Reasoning model — verify_how_lite.py uses max_tokens=4000 with empty-content
# retry. Reasoning models burn tokens on internal reasoning_tokens before
# emitting content, so anything below ~4000 returns finish_reason=length with
# empty content. judge_frontier_eng.py is already wired for reasoning models.
#
# Doc §6 + §10.2 — P0-W1 (2026-06-11) integrations:
#   1. Runs scripts/verify_routing_panel.py --strict BEFORE paired_ab.
#      A retrieval-correctness failure refuses launch (Gate A hard
#      gate). Skip with --skip-routing-panel for emergency hotfixes.
#   2. Exports ROSCLAW_GLM_API_KEY so judge_frontier_eng.py can fall
#      back to z.ai GLM-4.7-Flash when 302.ai step-3.7-flash returns
#      empty content / unreachable.
#
# Usage:
#   ./scripts/launch_paired_ab_302ai.sh \
#     --label iter4_p9_T001_T002_T003_T005_T008_TW002 \
#     --how-base http://127.0.0.1:47820 \
#     --seeds 1 2 3 4 5 6 7 8 9 10 \
#     --bundle-label iter4_p9 \
#     --temperature 0.3 \
#     --task-ids TASK_001_PIDTuning TASK_002_QuadrupedGait TASK_005_AES128_Throughput TASK_W_002_GradExplosionRL
set -euo pipefail

# 302.ai / z.ai credentials are read from environment or the repo's .env file.
# Do NOT hardcode secrets in this tracked launcher — .env is gitignored.
# .env.local (also gitignored) is sourced after .env so operators can override
# without editing the canonical .env file.
set -a
[ -f .env ] && . .env
[ -f .env.local ] && . .env.local
set +a

# step-3.7-flash is the FREE 302.ai model. Deepseek-chat is paid and the
# project key's balance was exhausted on 2026-06-10 — user directive
# 2026-06-10 mandates step-3.7-flash only going forward.
: "${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY must be set in env or .env}"
export DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.302.ai}"
export DEEPSEEK_MUSE_MODEL="${DEEPSEEK_MUSE_MODEL:-step-3.7-flash}"

# z.ai GLM-4.7-Flash — fallback judge provider. Doc §10.2: when 302.ai
# returns empty content (reasoning model burned budget) or unreachable,
# judge_frontier_eng.py falls back to this and tags judge_provider=z.ai.
if [ -n "${ROSCLAW_GLM_API_KEY:-}" ]; then
  export ROSCLAW_GLM_BASE_URL="${ROSCLAW_GLM_BASE_URL:-https://api.z.ai/api/paas/v4}"
  export ROSCLAW_GLM_MODEL="${ROSCLAW_GLM_MODEL:-GLM-4.7-Flash}"
fi

cd "$(dirname "$0")/.."

# Parse out --how-base + --skip-routing-panel from arg list (rest is
# forwarded to run_paired_ab.py verbatim). Bash arg parsing is verbose
# because some flags take a single value while others (--seeds,
# --task-ids) take many, so we don't try to fully understand the args
# — we just peek for the two we need.
HOW_BASE="${ROSCLAW_HOW_BASE:-http://127.0.0.1:8088}"
SKIP_ROUTING_PANEL=0
RUN_ARGS=()
i=0
args=("$@")
while [ "$i" -lt "${#args[@]}" ]; do
  arg="${args[$i]}"
  case "$arg" in
    --how-base)
      if [ "$((i+1))" -lt "${#args[@]}" ]; then
        HOW_BASE="${args[$((i+1))]}"
        RUN_ARGS+=("$arg" "${args[$((i+1))]}")
        i=$((i+2))
        continue
      fi
      ;;
    --skip-routing-panel)
      SKIP_ROUTING_PANEL=1
      i=$((i+1))
      continue
      ;;
  esac
  RUN_ARGS+=("$arg")
  i=$((i+1))
done

if [ "$SKIP_ROUTING_PANEL" -eq 0 ]; then
  echo "[launch_paired_ab] doc §6 Gate A — running routing_panel pre-check against $HOW_BASE"
  if ! PYTHONPATH=src .venv/bin/python scripts/verify_routing_panel.py \
      --base "$HOW_BASE" \
      --strict \
      --out "data/reports/routing_pre_paired_ab_$(date -u +%Y%m%dT%H%M%SZ).json"; then
    cat <<MSG >&2

[launch_paired_ab] REFUSED — routing_panel pre-check failed.

Gate A (retrieval correctness) is a HARD precondition for paired_ab. A failing
panel means HOW would route some tasks to wrong/no patterns; the resulting
LLM-judge scores would not reflect the curated changes under test.

Fix options:
  1. Address the routing failures (see data/reports/routing_pre_paired_ab_*.json
     and re-publish_to_how if curated content changes are needed).
  2. /admin/reload HOW if a fresh bridge isn't loaded.
  3. Bypass with --skip-routing-panel for EMERGENCY hotfixes only.
     A bypassed paired_ab run MUST NOT be cited as ship evidence.

MSG
    exit 2
  fi
  echo "[launch_paired_ab] routing_panel pre-check PASSED — proceeding to paired_ab"
fi

PYTHONPATH=src .venv/bin/python scripts/run_paired_ab.py "${RUN_ARGS[@]}"
