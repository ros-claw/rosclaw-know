#!/usr/bin/env bash
# orchestrate_iteration.sh — judge + aggregate for n=10 A/B
#
# Usage: orchestrate_iteration.sh <report_dir_relative_to_rosclaw-know>
#   e.g. orchestrate_iteration.sh data/benchmarks/baseline_n10
#
# 1. Spawn 10 parallel judges (5-concurrent) using step-3.7-flash via 302.ai
# 2. Wait
# 3. Aggregate per-task and across-seed stats
# Outputs: <report_dir>/_aggregate.txt

set -e
REPORT_DIR="$1"
if [ -z "$REPORT_DIR" ]; then
  echo "usage: $0 <report_dir>"
  exit 1
fi

ROSCLAW_KNOW="/root/workspace/rosclaw/rosclaw_wiki/rosclaw-know"
cd "$ROSCLAW_KNOW"

# 302.ai credentials: read from env / .env; never hardcode secrets in tracked scripts.
set -a
[ -f .env ] && . .env
set +a
: "${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY must be set in env or .env}"
export DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.302.ai}"
export DEEPSEEK_MUSE_MODEL="${DEEPSEEK_MUSE_MODEL:-step-3.7-flash}"

if [ ! -d "$REPORT_DIR" ]; then
  echo "ERROR: $REPORT_DIR not found"
  exit 1
fi

echo "=== Step 1: Verify all seed_*/summary.json present ==="
for S in $(seq 1 10); do
  if [ ! -f "$REPORT_DIR/seed_$S/summary.json" ]; then
    echo "MISSING: seed_$S/summary.json"
    exit 1
  fi
done
echo "All 10 summary.json present"

echo "=== Step 2: Spawn 10 parallel judges (5-concurrent) ==="
seq 1 10 | xargs -P 5 -I {} bash -c "
  .venv/bin/python scripts/judge_frontier_eng.py \
    --report-dir $REPORT_DIR/seed_{} --seed {} \
    > $REPORT_DIR/seed_{}.judge.log 2>&1 && echo \"judge seed_{} done\"
"
echo "=== All judges done ==="

echo "=== Step 3: Aggregate ==="
.venv/bin/python <<EOF > "$REPORT_DIR/_aggregate.txt"
import json
import statistics
from pathlib import Path

REPORT_DIR = Path("$REPORT_DIR")

# Per-seed stats
seed_data = []
for S in range(1, 11):
    d = REPORT_DIR / f"seed_{S}"
    summary = json.loads((d / "summary.json").read_text())
    uplifts = []
    verdicts = {"T": 0, "C": 0, "=": 0, "skip": 0}
    for entry in summary:
        jud = entry.get("judgment", {})
        c = (jud.get("control") or {}).get("score")
        t = (jud.get("treatment") or {}).get("score")
        if c is None or t is None:
            verdicts["skip"] += 1
            continue
        delta = t - c
        uplifts.append(delta)
        if delta > 0:
            verdicts["T"] += 1
        elif delta < 0:
            verdicts["C"] += 1
        else:
            verdicts["="] += 1
    if uplifts:
        avg = sum(uplifts) / len(uplifts)
        wr = verdicts["T"] / sum(verdicts.values()) * 100 if sum(verdicts.values()) else 0
        seed_data.append({"seed": S, "avg": avg, "wr": wr, "verdicts": verdicts, "n": len(uplifts)})
    else:
        print(f"seed_{S}: no valid uplifts")

print("=== Per-seed ===")
for sd in seed_data:
    v = sd["verdicts"]
    print(f"  seed_{sd['seed']:2d}: avg={sd['avg']:+.2f}  WR={sd['wr']:.0f}%  ({v['T']}T/{v['C']}C/{v['=']}=/{v['skip']}skip)  n={sd['n']}")

# Per-task stats
print()
print("=== Per-task (mean ± std across 10 seeds) ===")
tasks = {}
for S in range(1, 11):
    d = REPORT_DIR / f"seed_{S}"
    summary = json.loads((d / "summary.json").read_text())
    for entry in summary:
        tid = entry["task_id"]
        jud = entry.get("judgment", {})
        c = (jud.get("control") or {}).get("score")
        t = (jud.get("treatment") or {}).get("score")
        if c is None or t is None:
            continue
        tasks.setdefault(tid, []).append((t - c, entry.get("how_meta", {})))

# Sort: wild (TASK_NNN) first then home-turf (TASK_W_NNN)
task_ids = sorted(tasks.keys(), key=lambda k: (k.startswith("TASK_W"), k))
print(f"{'task_id':<40}{'mean Δ':>8}{'std Δ':>8}{'n':>4}{'  verdicts':<15}")
for tid in task_ids:
    deltas = [d for d, _ in tasks[tid]]
    if not deltas:
        continue
    mean = sum(deltas) / len(deltas)
    std = statistics.stdev(deltas) if len(deltas) > 1 else 0
    T = sum(1 for d in deltas if d > 0)
    C = sum(1 for d in deltas if d < 0)
    E = sum(1 for d in deltas if d == 0)
    print(f"{tid:<40}{mean:>+8.2f}{std:>8.2f}{len(deltas):>4}  {T}T/{C}C/{E}=")

# Overall
print()
print("=== Overall ===")
all_seed_avgs = [sd["avg"] for sd in seed_data]
overall_mean = sum(all_seed_avgs) / len(all_seed_avgs)
overall_std = statistics.stdev(all_seed_avgs) if len(all_seed_avgs) > 1 else 0
# 95% CI via t-distribution
import math
n = len(all_seed_avgs)
if n > 1:
    se = overall_std / math.sqrt(n)
    # t-value for 95% CI, df=n-1, approximate (t≈2.262 for df=9)
    t_val = 2.262 if n == 10 else 2.0
    ci_low = overall_mean - t_val * se
    ci_high = overall_mean + t_val * se
else:
    ci_low = ci_high = overall_mean

print(f"Seeds: {n}")
print(f"Across-seed mean Δ: {overall_mean:+.3f}  std={overall_std:.3f}  95% CI [{ci_low:+.2f}, {ci_high:+.2f}]")
all_seed_wrs = [sd["wr"] for sd in seed_data]
overall_wr = sum(all_seed_wrs) / len(all_seed_wrs)
print(f"Mean WR: {overall_wr:.1f}%")

# Panel split
wild = [d for tid in task_ids if not tid.startswith("TASK_W") for d, _ in tasks[tid]]
home = [d for tid in task_ids if tid.startswith("TASK_W") for d, _ in tasks[tid]]
if wild:
    wild_mean = sum(wild) / len(wild)
    wild_std = statistics.stdev(wild) if len(wild) > 1 else 0
    wild_se = wild_std / math.sqrt(len(wild))
    print(f"  Wild      n={len(wild)}: mean={wild_mean:+.3f} std={wild_std:.3f} CI [{wild_mean - 1.96*wild_se:+.2f}, {wild_mean + 1.96*wild_se:+.2f}]")
if home:
    home_mean = sum(home) / len(home)
    home_std = statistics.stdev(home) if len(home) > 1 else 0
    home_se = home_std / math.sqrt(len(home))
    print(f"  Home-turf n={len(home)}: mean={home_mean:+.3f} std={home_std:.3f} CI [{home_mean - 1.96*home_se:+.2f}, {home_mean + 1.96*home_se:+.2f}]")
EOF
echo "=== Aggregate written to $REPORT_DIR/_aggregate.txt ==="
cat "$REPORT_DIR/_aggregate.txt"
