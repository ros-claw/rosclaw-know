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

  task_id                       control  treatment  Δ  verdict
  TASK_001_PIDTuning            7        8          +1 treatment_better
  TASK_002_QuadrupedGait        6        9          +3 treatment_better
  ...
  ──────────────────────────────────────────────────────────────────
  Tasks scored: 10   treatment_better: 7   control_better: 2   tie: 1
  Pairwise win rate (treatment): 70%   (7/10)
  Average uplift  (treatment − control): +1.40
  Median uplift   (treatment − control): +1.5

This is the §5.5 ‘Frontier-Eng 统计指标’ smoke — one A/B run per task,
no seeding/budgeting yet — that gives an automated read on whether
bridge injection clearly hits the evaluation_hint vs the bare LLM.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
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


_SCORE_RX = re.compile(
    r"SCORE\s*[=:]\s*(\d+)(?:\s+REASON\s*[=:]\s*(.+))?",
    re.IGNORECASE,
)


async def _chat_seeded(
    session: aiohttp.ClientSession,
    *,
    system: str,
    user: str,
    seed: int,
    max_tokens: int = 4000,
    temperature: float = 0.0,
) -> str | None:
    """Seeded variant of rosclaw_know.llm.chat — required because the
    library helper doesn't accept a ``seed`` field yet and we don't
    want to modify code outside scripts/.

    Posts directly to the DeepSeek OpenAI-compatible
    ``/v1/chat/completions`` endpoint, including ``seed=<int>`` in
    the payload so paired judging runs across A/B arms share the
    judge's own sampling randomness.

    Robust to reasoning models (step-3.7-flash, deepseek v4-flash):
    when ``finish_reason=length`` and ``content`` is empty (reasoning
    burned the budget), retry once with a larger ``max_tokens``.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get(
        "DEEPSEEK_MUSE_MODEL",
        os.environ.get("DEEPSEEK_EXTRACTOR_MODEL", "deepseek-chat"),
    )
    if not api_key:
        return None
    # Two attempts: caller's budget first; double it on empty content
    for attempt in range(2):
        budget = max_tokens if attempt == 0 else max(max_tokens * 2, 16000)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": budget,
            "seed": int(seed),
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with session.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error("seeded judge LLM %s: %s", resp.status, body[:200])
                    if attempt == 0:
                        continue
                    return None
                data = await resp.json()
                choices = data.get("choices") or []
                if not choices:
                    if attempt == 0:
                        continue
                    return None
                msg = choices[0].get("message") or {}
                content = msg.get("content")
                if content:
                    return content
                # Empty content from reasoning model — retry with larger budget
                if attempt == 0:
                    logger.warning(
                        "judge empty content (likely reasoning ate budget); retrying with %d tokens",
                        max(max_tokens * 2, 16000),
                    )
                    continue
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("seeded judge network error (attempt %d): %s", attempt, exc)
            if attempt == 0:
                continue
            return None
    return None


async def _judge_one(
    session: aiohttp.ClientSession,
    *,
    symptom: str,
    hint: str,
    response_text: str,
    seed: int | None = None,
) -> dict:
    user = JUDGE_TEMPLATE.format(symptom=symptom, hint=hint, response=response_text)
    if seed is not None:
        raw = await _chat_seeded(
            session,
            system=JUDGE_SYSTEM,
            user=user,
            seed=seed,
            temperature=0.0,
            max_tokens=4000,
        )
    else:
        raw = await chat(
            session,
            system=JUDGE_SYSTEM,
            user=user,
            temperature=0.0,
            max_tokens=4000,
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
    reason = (m.group(2) or "").strip()[:200]
    return {"score": score, "reason": reason, "raw": raw}


def _verdict(c: int | None, t: int | None) -> str:
    if c is None or t is None:
        return "skipped"
    if t > c:
        return "treatment_better"
    if c > t:
        return "control_better"
    return "tie"


async def _judge_all(report_dir: Path, *, seed: int | None = None) -> dict:
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
                    session, symptom=symptom, hint=hint, response_text=ctrl, seed=seed
                ),
                "treatment": await _judge_one(
                    session, symptom=symptom, hint=hint, response_text=treat, seed=seed
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
# private state from it.  Keep in sync when expanding BENCHMARK_SUITE.
_SYMPTOM_BY_ID = {
    "TASK_001_PIDTuning": (
        "PID controller drives a robotic-arm joint into sustained oscillation when "
        "the integral term saturates. Latency from sensor to actuator is 30 ms."
    ),
    "TASK_002_QuadrupedGait": (
        "A quadruped robot's trot-gait policy diverges on uneven terrain: foot "
        "slip events cause the center-of-mass tracking error to grow each cycle "
        "until the robot falls within ~3 seconds of stepping onto loose gravel."
    ),
    "TASK_003_RobotArmCycleTime": (
        "A 6-DOF pick-and-place robot arm has a 4.2 s cycle time but the customer "
        "needs <=3.0 s. Profiling shows 60% of the cycle is joint-space motion that "
        "decelerates to zero between via-points, even when the via-points are colinear."
    ),
    "TASK_004_HighReliableSimulation": (
        "A reliability simulation of a redundant power-electronics inverter needs "
        "to estimate p(failure) ~ 1e-8 per operating hour. Naive Monte Carlo on "
        "10^6 samples returns 0 failures and gives no useful estimate."
    ),
    "TASK_005_AES128_Throughput": (
        "An AES-128-CBC implementation in pure C achieves 80 MB/s on a modern "
        "x86_64 server, but the requirement is 1 GB/s. Profiling shows the inner "
        "SubBytes/MixColumns loop dominates."
    ),
    "TASK_006_FlashAttention": (
        "A transformer's self-attention layer hits CUDA OOM at 8K context length "
        "because the NxN attention matrix is materialized in HBM.  Inference "
        "throughput is also bandwidth-bound, not compute-bound."
    ),
    "TASK_007_BatteryFastCharging": (
        "A Li-ion fast-charging profile that delivers 4C constant-current from "
        "10%->80% SOC accelerates capacity fade to >2% per 100 cycles on "
        "graphite anodes, far above the 0.5%/100c spec."
    ),
    "TASK_008_JobShop_abz": (
        "A job-shop scheduler on the abz5 benchmark instance produces makespans "
        "around 1400 (known optimum 1234) with a greedy dispatch rule and "
        "stagnates after 10K iterations of local search."
    ),
    "TASK_009_TopologyOptimization": (
        "A SIMP-based topology optimizer for a 2D cantilever-beam compliance "
        "problem produces checkerboard artifacts and mesh-dependent solutions: "
        "halving the element size doubles the apparent number of struts."
    ),
    "TASK_010_UAVInspection": (
        "A quadrotor inspecting a wind-turbine blade with an onboard RGB camera "
        "produces motion-blurred frames at the tip flyby (15 m/s relative "
        "velocity, 100 mm focal length, 1/120s shutter), so defect detection "
        "recall drops from 0.9 (stationary) to 0.4 (flyby)."
    ),
    # Home-turf panel (8 tasks each mapping to a curated/muse pattern;
    # kept in sync with scripts/verify_frontier_eng.py).
    "TASK_W_001_KVCacheLongContext": (
        "A code-assistant LLM serving multi-turn programming sessions accumulates "
        "32K tokens of conversation history after 20 turns, hitting the model's 16K "
        "context window. Per-turn latency grows from 800ms to 3.5s as the running "
        "state fills."
    ),
    "TASK_W_002_GradExplosionRL": (
        "A PPO policy network training on a robotic manipulation task shows the "
        "reward curve diverging to NaN around training step 50K.  Loss values "
        "were stable until step 47K then spiked to 1e8 within 200 updates; "
        "gradient norms in the actor head are unbounded."
    ),
    "TASK_W_003_NetRetryStorm": (
        "A microservice fetching upstream API data sees 95% of calls succeed but "
        "5% return 503.  Naive retry-on-failure with 100ms fixed delay causes "
        "cascading load: when upstream is degraded, retries amplify traffic ~10x, "
        "prolonging the outage."
    ),
    "TASK_W_004_EntropyCollapsePPO": (
        "A PPO agent training on a stochastic environment converges to a "
        "deterministic policy by episode 5000.  Action entropy drops to "
        "near zero, exploration ceases, and the policy gets stuck 30% below "
        "SOTA reward.  Hyperparameters were copied from a known-good benchmark."
    ),
    "TASK_W_005_ActuatorOvershoot": (
        "A linear voice-coil actuator commanded with PID + feedforward overshoots "
        "its rated 25 N peak force by 18% during fast setpoint changes, triggering "
        "the mechanical end-stops and dropping the position lock.  The command "
        "signal itself briefly exceeds the rated peak before the safety relay "
        "trips."
    ),
    "TASK_W_006_PlanningDivergence": (
        "A model-predictive controller running at 50 Hz computes a 2-second-"
        "horizon trajectory assuming nominal dynamics, but actual ground friction "
        "varies by 3x across the operating area.  By the planning horizon's end, "
        "the predicted state differs from the measured state by >20 cm, and "
        "tracking error accumulates unboundedly between re-plans."
    ),
    "TASK_W_007_IntegrationWindup": (
        "A flow-rate PID controller saturates its control valve fully open during "
        "a long demand transient.  After the setpoint returns to normal, the "
        "controller takes 8 seconds to release accumulated integrator state, "
        "causing 25% overshoot past target as the valve eventually closes."
    ),
    "TASK_W_008_AttentionMemoryOOM": (
        "A transformer model serving 4K-context inference on a 24 GB GPU hits "
        "OOM at batch size 8.  Memory profiling shows the N×N attention "
        "probability matrix taking 12 GB just for the full sequence, scaling "
        "quadratically with context length."
    ),
}


def _print_report(summary: list[dict]) -> int:
    print(f"{'task_id':<30} {'control':>7} {'treatment':>9} {'Δ':>4} verdict")
    deltas: list[int] = []
    verdicts: list[str] = []
    for entry in summary:
        j = entry.get("judgment", {})
        c = j.get("control", {}).get("score")
        t = j.get("treatment", {}).get("score")
        d = (t - c) if (c is not None and t is not None) else None
        if d is not None:
            deltas.append(d)
        verdicts.append(j.get("verdict", "?"))
        d_str = f"{d:+d}" if d is not None else "  - "
        print(
            f"{entry['task_id']:<30} "
            f"{('-' if c is None else c):>7} "
            f"{('-' if t is None else t):>9} "
            f"{d_str:>4} {j.get('verdict','?')}"
        )
    print("─" * 70)

    if not deltas:
        print("No scored tasks — judge returned no usable scores.")
        return 1

    n_total = len(verdicts)
    n_treat = verdicts.count("treatment_better")
    n_ctrl = verdicts.count("control_better")
    n_tie = verdicts.count("tie")
    avg = statistics.mean(deltas)
    med = statistics.median(deltas)

    # Pairwise win rate per outline §5.5: treatment_better / total
    # (ties don't count as wins, but they don't count as losses either,
    #  so the denominator stays as the full panel).
    win_rate = n_treat / n_total

    # The outline (§5.6 phase-1 acceptance) sets pairwise win rate ≥ 55%
    # as the smoke-acceptance bar for "hermes_how_only > hermes_no_knowhow".
    # On a 10-task panel ≥6/10 wins clears the bar; ≥5 wins is borderline.
    print(
        f"Tasks scored: {n_total}   treatment_better: {n_treat}   "
        f"control_better: {n_ctrl}   tie: {n_tie}"
    )
    print(f"Pairwise win rate (treatment): {win_rate:.0%}   ({n_treat}/{n_total})")
    print(f"Average uplift  (treatment − control): {avg:+.2f}")
    print(f"Median uplift   (treatment − control): {med:+.1f}")

    if win_rate >= 0.55 and avg > 0:
        print("PASS: outline §5.6 phase-1 smoke bar met (win rate ≥55%, avg uplift > 0).")
        return 0

    print(
        "WARNING: did not clear outline §5.6 phase-1 bar (win rate ≥55% AND avg uplift > 0). "
        "Manual review of per-task verdicts above."
    )
    # Don't fail CI on a single-seed run — the outline calls for 5 seeds
    # before a hard verdict.  Return 0 and let the operator decide.
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--report-dir",
        type=Path,
        default=BENCHMARKS_DIR / "frontier_eng_ab",
        help="Output dir of verify_frontier_eng.py.",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "Integer seed forwarded as the OpenAI-compatible ``seed`` "
            "field in the DeepSeek judge payload. When set, paired "
            "judging runs across A/B arms share the judge's own "
            "sampling randomness so judge-noise cancels in the paired Δ."
        ),
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if not args.report_dir.exists():
        logger.error("report dir %s missing — run verify_frontier_eng.py first", args.report_dir)
        return 1

    summary = asyncio.run(_judge_all(args.report_dir, seed=args.seed))
    return _print_report(summary)


if __name__ == "__main__":
    sys.exit(main())
