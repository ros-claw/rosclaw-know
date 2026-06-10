#!/usr/bin/env bash
# launch_paired_ab_302ai.sh — orchestrate_iteration.sh's sibling for VERIFY.
#
# Project's verify_frontier_eng.py reads DEEPSEEK_API_KEY/BASE_URL from
# os.environ at call time. The repo's .env currently points to
# api.deepseek.com (which 402s on the configured key), so paired_ab runs
# need the 302.ai override that orchestrate_iteration.sh already encodes
# for the judge side. Same key, same project convention — see
# scripts/orchestrate_iteration.sh:38 for the canonical declaration.
#
# Usage:
#   ./scripts/launch_paired_ab_302ai.sh \
#     --label iter4_p3_T001_T002_T005_TW002 \
#     --how-base http://127.0.0.1:47820 \
#     --seeds 1 2 3 4 5 6 7 8 9 10 \
#     --bundle-label iter4_p3 \
#     --temperature 0.3 \
#     --task-ids TASK_001_PIDTuning TASK_002_QuadrupedGait TASK_005_AES128_Throughput TASK_W_002_GradExplosionRL
#
# This launcher exports the 302.ai credentials before invoking
# run_paired_ab.py so verify_frontier_eng's _call_agent hits the
# 302.ai-backed deepseek-chat (same endpoint iter4_p1/p2 used).
set -euo pipefail

# 302.ai credentials — same as scripts/orchestrate_iteration.sh:38.
export DEEPSEEK_API_KEY=sk-2sXFHpM70jnSQOctr0ckiJsw0xWHfAbnw07FDCHepi8Uhhhf
export DEEPSEEK_BASE_URL=https://api.302.ai
export DEEPSEEK_MUSE_MODEL=deepseek-chat

cd "$(dirname "$0")/.."
PYTHONPATH=src .venv/bin/python scripts/run_paired_ab.py "$@"
