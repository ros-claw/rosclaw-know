#!/usr/bin/env python3
"""scripts/judge_frontier_eng.py — auto-judge for verify_frontier_eng A/B outputs.

Reads:
  data/benchmarks/frontier_eng_ab/summary.json
  data/benchmarks/frontier_eng_ab/<task_id>.control.txt
  data/benchmarks/frontier_eng_ab/<task_id>.treatment.txt

For each task, asks a DeepSeek "judge" to score each response (0-10) against
the task's ``evaluation_hint``, with temperature 0 for near-determinism.
Writes the judgments back into ``summary.json`` as a sibling array
``judgments`` per task.

Final stdout report:

  task_id              control  treatment  Δ  verdict
  ERR_001_PID_runaway  6        9          +3 treatment_better
  ERR_002_CUDA_OOM     7        7           0 tie
  ───────────────────────────────────────────────────────
  Average uplift (treatment − control): +1.5

This is the §5.5 ‘Frontier-Eng 统计指标’ smoke — one A/B run per task,
no seeding/budgeting yet — that gives an automated read on whether
bridge injection clearly hits the evaluation_hint vs the bare LLM.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import statistics
import sys
from pathlib import Path

import aiohttp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.config import BENCHMARKS_DIR  # noqa: E402
from rosclaw_know.llm import chat  # noqa: E402

logger = logging.getLogger("rosclaw_know.judge_frontier_eng")

JUDGE_SYSTEM = (
    "You are an expert engineering reviewer scoring a candidate response "
    "against a known evaluation hint. Respond ONLY in the exact format "
    "specified — no preamble, no explanation outside the format."
)

JUDGE_TEMPLATE = """Task description:
{symptom}

Evaluation hint (what a good response MUST include or convincingly address):
{hint}

Candidate response:
\"\"\"
{response}
\"\"\"

Score the candidate on a 0-10 integer scale by how clearly it hits the
evaluation hint. Reserve 9-10 for responses that name the exact concept
the hint mentions AND give a concrete fix; 6-8 for responses that hint
at the right family of fixes; 3-5 for responses that miss the named
concept but propose a related fix; 0-2 for responses that misdiagnose
the problem.

Reply in this format on a SINGLE line, nothing else:
SCORE=<int 0-10>  REASON=<one short sentence, <120 chars>
"""


_SCORE_RX = re.compile(r"SCORE\s*[=:]\s*(\d+)\s*REASON\s*[=:]\s*(.+)", re.IGNORECASE)


async def _judge_one(
    session: aiohttp.ClientSession,
    *,
    symptom: str,
    hint: str,
    response_text: str,
) -> dict:
    user = JUDGE_TEMPLATE.format(symptom=symptom, hint=hint, response=response_text)
    raw = await chat(
        session,
        system=JUDGE_SYSTEM,
        user=user,
        temperature=0.0,
        max_tokens=200,
    )
    if not raw:
        return {"score": None, "reason": "judge returned empty", "raw": raw}
    m = _SCORE_RX.search(raw.strip())
    if not m:
        return {"score": None, "reason": f"unparseable: {raw[:160]}", "raw": raw}
    try:
        score = max(0, min(10, int(m.group(1))))
    except ValueError:
        return {"score": None, "reason": f"non-int score: {m.group(1)}", "raw": raw}
    return {"score": score, "reason": m.group(2).strip()[:200], "raw": raw}


def _verdict(c: int | None, t: int | None) -> str:
    if c is None or t is None:
        return "skipped"
    if t > c:
        return "treatment_better"
    if c > t:
        return "control_better"
    return "tie"


async def _judge_all(report_dir: Path) -> dict:
    summary_path = report_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"missing {summary_path} — run verify_frontier_eng.py first")
    summary = json.loads(summary_path.read_text())

    async with aiohttp.ClientSession() as session:
        for entry in summary:
            task_id = entry["task_id"]
            hint = entry["evaluation_hint"]
            # verify_frontier_eng stores only first_200 in summary, so re-read
            # the full control/treatment files from disk.
            ctrl_path = report_dir / f"{task_id}.control.txt"
            treat_path = report_dir / f"{task_id}.treatment.txt"
            ctrl = ctrl_path.read_text() if ctrl_path.exists() else ""
            treat = treat_path.read_text() if treat_path.exists() else ""
            # We don't have the original symptom in summary — re-derive it
            # via the deterministic BENCHMARK_SUITE.
            symptom = _SYMPTOM_BY_ID.get(task_id, "")

            entry["judgment"] = {
                "control": await _judge_one(
                    session, symptom=symptom, hint=hint, response_text=ctrl
                ),
                "treatment": await _judge_one(
                    session, symptom=symptom, hint=hint, response_text=treat
                ),
            }
            entry["judgment"]["verdict"] = _verdict(
                entry["judgment"]["control"]["score"],
                entry["judgment"]["treatment"]["score"],
            )

    # Strip raw judge replies before writing — they bloat the file and
    # leak prompt internals. Keep score + reason.
    for entry in summary:
        for side in ("control", "treatment"):
            entry["judgment"][side].pop("raw", None)

    summary_path.write_text(json.dumps(summary, indent=2))
    return summary


# Pulled from scripts/verify_frontier_eng.py so we don't have to read
# private state from it.
_SYMPTOM_BY_ID = {
    "ERR_001_PID_runaway": (
        "PID controller drives a robotic-arm joint into sustained oscillation when "
        "the integral term saturates. Latency from sensor to actuator is 30 ms."
    ),
    "ERR_002_CUDA_OOM": (
        "A vision-language-navigation model's KV-cache grows linearly with "
        "trajectory length, causing CUDA OOM after ~800 steps."
    ),
}


def _print_report(summary: list[dict]) -> int:
    print(f"{'task_id':<26} {'control':>7} {'treatment':>9} {'Δ':>4} verdict")
    deltas = []
    for entry in summary:
        j = entry.get("judgment", {})
        c = j.get("control", {}).get("score")
        t = j.get("treatment", {}).get("score")
        d = (t - c) if (c is not None and t is not None) else None
        if d is not None:
            deltas.append(d)
        d_str = f"{d:+d}" if d is not None else "  - "
        print(
            f"{entry['task_id']:<26} "
            f"{('-' if c is None else c):>7} "
            f"{('-' if t is None else t):>9} "
            f"{d_str:>4} {j.get('verdict','?')}"
        )
    print("─" * 60)
    if deltas:
        avg = statistics.mean(deltas)
        print(f"Average uplift (treatment − control): {avg:+.2f}  (n={len(deltas)})")
        if avg > 0:
            return 0
        # An average <= 0 across tasks is a meaningful smoke-test failure,
        # but we don't want to break CI on a 2-task panel — flag it loud
        # and return 0 so the operator can decide.  Tighten when the
        # benchmark suite grows past ~10 tasks.
        print("WARNING: treatment did not beat control on average — manual review.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--report-dir",
        type=Path,
        default=BENCHMARKS_DIR / "frontier_eng_ab",
        help="Output dir of verify_frontier_eng.py.",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if not args.report_dir.exists():
        logger.error("report dir %s missing — run verify_frontier_eng.py first", args.report_dir)
        return 1

    summary = asyncio.run(_judge_all(args.report_dir))
    return _print_report(summary)


if __name__ == "__main__":
    sys.exit(main())
