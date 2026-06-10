#!/usr/bin/env python3
"""LLM-baseline headroom probe.

docs/know-how下一步建议.md §6 — before authoring new curated patterns for
a target task, probe the LLM-unaided baseline. Per
project_llm_knowledge_ceiling memory: adding curated to a task with
LLM-baseline ≥ 9.0 is net-zero (T_006 FlashAttention curated at sim=0.860
still tied 10/10 — the LLM already knew the canonical fix).

This script:

  1. Imports the inline ``TASKS`` list from ``scripts/verify_frontier_eng.py``
  2. Filters to the requested ``--task-ids`` (regex OR exact match)
  3. Runs N seeds of the *control* arm — `_call_agent(symptom, treatment_context="")` —
     so the LLM gets ZERO injection from rosclaw-how. Pure unaided baseline.
  4. Scores each reply with a small synchronous reimplementation of
     ``judge_frontier_eng`` (same SCORE=<int>/REASON=<...> format).
  5. Aggregates per-task: control_mean, control_std, headroom = 10 - mean
  6. Emits a verdict per task:

        headroom ≥ 2.0   AUTHOR_CURATED — meaningful gap, curated may help
        1.0 ≤ headroom < 2.0   MAYBE — judge variance noise possible at this gap
        headroom < 1.0   SKIP_CURATED — LLM already saturates; injection ≈ 0

Usage::

    python scripts/probe_headroom.py \\
      --task-ids TASK_W_002_GradExplosionRL TASK_006_FlashAttention \\
      --seeds 1 2 3 4 5 \\
      --out probes/headroom_round_$(date +%Y%m%d).json

Cost: 2× LLM call per (task, seed) (one agent, one judge). ~25s × 2 ×
n_tasks × n_seeds. For 2 tasks × 5 seeds that's ~4 min on 302.ai-deepseek-chat.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know import config  # noqa: E402

# Load verify_frontier_eng.py as a module so we can pull TASKS + _call_agent
_VERIFY_PATH = PROJECT_ROOT / "scripts" / "verify_frontier_eng.py"


def _load_verify_module():
    spec = importlib.util.spec_from_file_location("verify_frontier_eng", _VERIFY_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# Judge template — same shape as scripts/judge_frontier_eng.py, simplified to
# a sync urllib call so probe_headroom has no aiohttp dependency.
_JUDGE_SYSTEM = (
    "You are an expert engineering reviewer scoring a candidate response "
    "against a known evaluation hint. Respond ONLY in the exact format "
    "specified — no preamble, no explanation outside the format."
)
_JUDGE_TEMPLATE = """Task description:
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

_SCORE_RX = re.compile(r"SCORE\s*[=:]\s*(\d+)(?:\s+REASON\s*[=:]\s*(.+))?", re.IGNORECASE)


def _judge_one(symptom: str, hint: str, response_text: str, seed: int) -> dict[str, Any]:
    """Synchronous single-judge call. Returns {score, reason, raw}."""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("DEEPSEEK_MUSE_MODEL", "deepseek-chat")
    if not api_key:
        return {"score": None, "reason": "DEEPSEEK_API_KEY not set", "raw": ""}

    user = _JUDGE_TEMPLATE.format(symptom=symptom, hint=hint, response=response_text)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "max_tokens": 4000,
        "seed": int(seed),
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"score": None, "reason": f"HTTP {exc.code}", "raw": ""}
    except Exception as exc:  # noqa: BLE001
        return {"score": None, "reason": f"net error: {exc}", "raw": ""}

    raw = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    m = _SCORE_RX.search(raw)
    if not m:
        return {"score": None, "reason": f"unparseable: {raw[:160]}", "raw": raw}
    try:
        return {
            "score": max(0, min(10, int(m.group(1)))),
            "reason": (m.group(2) or "").strip(),
            "raw": raw,
        }
    except ValueError:
        return {"score": None, "reason": f"non-int score: {m.group(1)}", "raw": raw}


def _verdict(headroom: float) -> str:
    if headroom >= 2.0:
        return "AUTHOR_CURATED"
    if headroom >= 1.0:
        return "MAYBE"
    return "SKIP_CURATED"


def _resolve_task_ids(verify_mod, requested: list[str]) -> list[dict[str, Any]]:
    """Filter the inline task list. ``requested`` may be exact IDs or regex.

    The verify script names its inline list ``BENCHMARK_SUITE`` (or
    ``TASKS`` in test fixtures); both are accepted so this script can be
    unit-tested with a thin fake module.
    """
    all_tasks = getattr(verify_mod, "BENCHMARK_SUITE", None)
    if all_tasks is None:
        all_tasks = getattr(verify_mod, "TASKS", None)
    if all_tasks is None:
        raise SystemExit(
            "[probe-headroom] verify module exposes neither BENCHMARK_SUITE nor TASKS"
        )
    if not requested:
        return list(all_tasks)
    out = []
    patterns = [re.compile(p) for p in requested]
    for t in all_tasks:
        tid = t["task_id"]
        if tid in requested or any(p.search(tid) for p in patterns):
            out.append(t)
    return out


def probe(
    *,
    task_ids: list[str],
    seeds: list[int],
    temperature: float,
    skip_judge: bool,
) -> dict[str, Any]:
    verify = _load_verify_module()
    targets = _resolve_task_ids(verify, task_ids)
    if not targets:
        raise SystemExit(f"[probe-headroom] no tasks matched {task_ids!r}")

    print(f"[probe-headroom] probing {len(targets)} tasks × {len(seeds)} seeds")
    for t in targets:
        print(f"  • {t['task_id']}")

    per_task: dict[str, dict[str, Any]] = {}
    for t in targets:
        tid = t["task_id"]
        rows = []
        for seed in seeds:
            reply = verify._call_agent(
                t["symptom"], treatment_context="", temperature=temperature, seed=seed
            )
            row: dict[str, Any] = {"seed": seed, "reply_len": len(reply)}
            if not skip_judge:
                row["judge"] = _judge_one(t["symptom"], t["evaluation_hint"], reply, seed)
                row["score"] = row["judge"]["score"]
            rows.append(row)
            score_str = (
                f"  score={row.get('score')}" if "score" in row else ""
            )
            print(f"  {tid:38s} seed={seed:2d}  reply_len={row['reply_len']:5d}{score_str}")

        valid_scores = [r["score"] for r in rows if r.get("score") is not None]
        agg = {
            "task_id": tid,
            "seeds_run": len(rows),
            "valid_scores": len(valid_scores),
            "rows": rows,
        }
        if valid_scores and not skip_judge:
            mean_ = statistics.mean(valid_scores)
            std_ = statistics.stdev(valid_scores) if len(valid_scores) > 1 else 0.0
            headroom = max(0.0, 10.0 - mean_)
            agg.update(
                {
                    "control_mean": round(mean_, 3),
                    "control_std": round(std_, 3),
                    "headroom": round(headroom, 3),
                    "verdict": _verdict(headroom),
                }
            )
        per_task[tid] = agg

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task_count": len(targets),
        "seeds": seeds,
        "temperature": temperature,
        "skip_judge": skip_judge,
        "deepseek_base_url": config.DEEPSEEK_BASE_URL,
        "deepseek_muse_model": config.DEEPSEEK_MUSE_MODEL,
        "per_task": per_task,
    }


def _print_summary(report: dict[str, Any]) -> None:
    print()
    print("=" * 72)
    print("Headroom summary")
    print("=" * 72)
    header = f"{'task_id':38s}  {'n':>2s}  {'mean':>5s}  {'std':>5s}  {'h':>5s}  verdict"
    print(header)
    print("-" * 72)
    for tid, agg in report["per_task"].items():
        if "control_mean" in agg:
            print(
                f"{tid:38s}  {agg['valid_scores']:2d}  "
                f"{agg['control_mean']:5.2f}  {agg['control_std']:5.2f}  "
                f"{agg['headroom']:5.2f}  {agg['verdict']}"
            )
        else:
            print(f"{tid:38s}  {agg['seeds_run']:2d}  (no judge — control reply only)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--task-ids",
        nargs="*",
        default=[],
        help="Exact task IDs or regex patterns. Empty = all 18 panel tasks.",
    )
    ap.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3, 4, 5])
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--skip-judge", action="store_true", help="Skip judging — print reply lens only.")
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "data" / "benchmarks" / "headroom" / "latest.json",
        help="Output JSON path (parent dir auto-created).",
    )
    args = ap.parse_args()

    report = probe(
        task_ids=args.task_ids,
        seeds=args.seeds,
        temperature=args.temperature,
        skip_judge=args.skip_judge,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[probe-headroom] report → {args.out}")
    _print_summary(report)

    # Exit non-zero if ANY task scored AUTHOR_CURATED — useful for CI/wrapper
    # scripts that gate "is there a worthwhile target to author for".
    has_target = any(
        agg.get("verdict") == "AUTHOR_CURATED" for agg in report["per_task"].values()
    )
    return 0 if has_target else 1


if __name__ == "__main__":
    raise SystemExit(main())
