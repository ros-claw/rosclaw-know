#!/usr/bin/env python3
"""Closed-loop A/B verification against the Frontier-Engineering benchmark.

Runs the same task twice — once with bridge_index.json injected into the
agent's system prompt (treatment), once without (control). Captures both
outputs to data/benchmarks/frontier_eng_ab/ for human comparison.

This is the Phase 1 "core hypothesis" check: does procedural knowledge
actually move the needle on Frontier-Eng tasks?
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know.config import ASSETS_DIR, BENCHMARKS_DIR, ensure_dirs  # noqa: E402

# Frontier-Eng smoke suite — 10 tasks chosen to span the categories in
# the test outline §5.2 (control, sim, systems-perf, high-reliability,
# energy, combinatorial, structural, inspection).  Each task pairs a
# concrete failure scenario with a sharp evaluation_hint so a judge can
# score 0-10 against an unambiguous rubric.
BENCHMARK_SUITE = [
    {
        "task_id": "TASK_001_PIDTuning",
        "symptom": (
            "PID controller drives a robotic-arm joint into sustained oscillation when "
            "the integral term saturates. Latency from sensor to actuator is 30 ms."
        ),
        "evaluation_hint": "should mention anti-windup clamp / derivative-on-measurement / gain scheduling",
    },
    {
        "task_id": "TASK_002_QuadrupedGait",
        "symptom": (
            "A quadruped robot's trot-gait policy diverges on uneven terrain: foot "
            "slip events cause the center-of-mass tracking error to grow each cycle "
            "until the robot falls within ~3 seconds of stepping onto loose gravel."
        ),
        "evaluation_hint": (
            "should propose foot-contact-aware MPC OR domain-randomized RL training "
            "with terrain perturbations OR a slip-detection reflex that re-plans "
            "the swing foot trajectory"
        ),
    },
    {
        "task_id": "TASK_003_RobotArmCycleTime",
        "symptom": (
            "A 6-DOF pick-and-place robot arm has a 4.2 s cycle time but the customer "
            "needs <=3.0 s. Profiling shows 60% of the cycle is joint-space motion that "
            "decelerates to zero between via-points, even when the via-points are colinear."
        ),
        "evaluation_hint": (
            "should propose trajectory blending / time-optimal path parameterization "
            "(TOPP) / via-point smoothing so the arm never stops at intermediate poses, "
            "OR jerk-limited S-curve profiles that hold acceleration through blends"
        ),
    },
    {
        "task_id": "TASK_004_HighReliableSimulation",
        "symptom": (
            "A reliability simulation of a redundant power-electronics inverter needs "
            "to estimate p(failure) ~ 1e-8 per operating hour. Naive Monte Carlo on "
            "10^6 samples returns 0 failures and gives no useful estimate."
        ),
        "evaluation_hint": (
            "should propose importance sampling / subset simulation / cross-entropy "
            "method / splitting (RESTART) - i.e. a rare-event variance-reduction "
            "technique, NOT just 'run more samples'"
        ),
    },
    {
        "task_id": "TASK_005_AES128_Throughput",
        "symptom": (
            "An AES-128-CBC implementation in pure C achieves 80 MB/s on a modern "
            "x86_64 server, but the requirement is 1 GB/s. Profiling shows the inner "
            "SubBytes/MixColumns loop dominates."
        ),
        "evaluation_hint": (
            "should propose AES-NI / vectorized intrinsics (_mm_aesenc_si128) OR "
            "GCM/CTR mode with hardware PCLMULQDQ, AND mention bitslicing as the "
            "portable fallback when AES-NI is unavailable"
        ),
    },
    {
        "task_id": "TASK_006_FlashAttention",
        "symptom": (
            "A transformer's self-attention layer hits CUDA OOM at 8K context length "
            "because the NxN attention matrix is materialized in HBM.  Inference "
            "throughput is also bandwidth-bound, not compute-bound."
        ),
        "evaluation_hint": (
            "should propose FlashAttention-style tiled / online-softmax attention "
            "that keeps the softmax computation in SRAM and never materializes the "
            "full attention matrix, OR sliding-window / circular-buffer KV-cache "
            "truncation as a second-best"
        ),
    },
    {
        "task_id": "TASK_007_BatteryFastCharging",
        "symptom": (
            "A Li-ion fast-charging profile that delivers 4C constant-current from "
            "10%->80% SOC accelerates capacity fade to >2% per 100 cycles on "
            "graphite anodes, far above the 0.5%/100c spec."
        ),
        "evaluation_hint": (
            "should propose multi-stage CC-CV with SOC-dependent current taper, "
            "OR pulse charging with rest intervals, OR temperature-aware current "
            "limiting - the underlying mechanism is avoiding lithium plating at "
            "high SOC"
        ),
    },
    {
        "task_id": "TASK_008_JobShop_abz",
        "symptom": (
            "A job-shop scheduler on the abz5 benchmark instance produces makespans "
            "around 1400 (known optimum 1234) with a greedy dispatch rule and "
            "stagnates after 10K iterations of local search."
        ),
        "evaluation_hint": (
            "should propose tabu search with critical-path moves / disjunctive-graph "
            "neighborhood, OR a genetic-algorithm crossover designed for JSP "
            "(e.g. operation-based or precedence-preserving), OR simulated annealing "
            "with shift moves - NOT just 'try a better dispatch rule'"
        ),
    },
    {
        "task_id": "TASK_009_TopologyOptimization",
        "symptom": (
            "A SIMP-based topology optimizer for a 2D cantilever-beam compliance "
            "problem produces checkerboard artifacts and mesh-dependent solutions: "
            "halving the element size doubles the apparent number of struts."
        ),
        "evaluation_hint": (
            "should propose density filtering / sensitivity filtering / Helmholtz "
            "PDE filter / projection schemes (Heaviside / threshold) to enforce "
            "mesh-independent length scale and eliminate checkerboarding"
        ),
    },
    {
        "task_id": "TASK_010_UAVInspection",
        "symptom": (
            "A quadrotor inspecting a wind-turbine blade with an onboard RGB camera "
            "produces motion-blurred frames at the tip flyby (15 m/s relative "
            "velocity, 100 mm focal length, 1/120s shutter), so defect detection "
            "recall drops from 0.9 (stationary) to 0.4 (flyby)."
        ),
        "evaluation_hint": (
            "should propose either (a) shorter exposure with higher ISO / global-"
            "shutter sensor, (b) hover-and-stare waypoints replacing the continuous "
            "flyby, OR (c) per-frame deblurring (Wiener / RL-based) using IMU-"
            "predicted blur kernels"
        ),
    },
]


def _build_treatment_prompt(bridge_index_path: Path) -> str:
    if not bridge_index_path.exists():
        return ""
    data = json.loads(bridge_index_path.read_text(encoding="utf-8"))
    clusters = data.get("symptom_clusters", {})
    if not clusters:
        return ""
    # Compact, agent-friendly digest (full json is too noisy)
    lines = ["[ROSCLAW cross-domain heuristic index (digested)]"]
    for i, (node, info) in enumerate(clusters.items()):
        if i >= 12:
            break
        lines.append(f"- {info['standard_name']}  [{info['domain']}]")
        for ana in info["cross_domain_analogies"][:2]:
            lines.append(f"    • {ana['source_domain']} → {ana['insight']}")
    return "\n".join(lines)


def _call_agent(symptom: str, treatment_context: str = "") -> str:
    """Call DeepSeek chat as a stand-in agent. Returns the raw assistant reply.

    The "agent" is intentionally simple — a single chat completion — because
    Phase 1 just needs to show that *prompting* the agent with the procedural
    index improves the answer quality, not that we have an autonomous loop yet.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    if not api_key:
        return "[mock-agent reply — DEEPSEEK_API_KEY not set]"

    import urllib.request

    system_prompt = (
        "You are a code-optimisation and debug agent for embodied-AI engineering. "
        "Given an engineering symptom, propose a concrete, code-level fix with safety constraints."
    )
    user_content = f"Engineering symptom:\n{symptom}\n"
    if treatment_context:
        user_content += (
            "\n\nThe ROSCLAW heuristic index has matched the following "
            "cross-domain analogies. Use them when relevant.\n"
            + treatment_context
            + "\n\nReturn a fix plan with: (1) immediate code change, (2) physical/safety "
              "constraints to enforce, (3) verification step."
        )

    payload = json.dumps(
        {
            "model": os.environ.get("DEEPSEEK_MUSE_MODEL", "deepseek-chat"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.0,
            "max_tokens": 600,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = resp.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        return f"[agent call failed: {exc}]"
    try:
        return json.loads(body)["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        return f"[agent parse failed: {body[:200]}]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=BENCHMARKS_DIR / "frontier_eng_ab")
    ap.add_argument("--bridge-path", type=Path, default=ASSETS_DIR / "bridge_index.json")
    args = ap.parse_args()

    ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    treatment_context = _build_treatment_prompt(args.bridge_path)
    print(f"Bridge context loaded: {len(treatment_context.splitlines())} lines\n")

    report = []
    for task in BENCHMARK_SUITE:
        print(f"▶ {task['task_id']}")
        control = _call_agent(task["symptom"])
        treatment = _call_agent(task["symptom"], treatment_context=treatment_context)

        (args.out_dir / f"{task['task_id']}.control.txt").write_text(control, encoding="utf-8")
        (args.out_dir / f"{task['task_id']}.treatment.txt").write_text(treatment, encoding="utf-8")

        report.append(
            {
                "task_id": task["task_id"],
                "evaluation_hint": task["evaluation_hint"],
                "control_first_200": control[:200],
                "treatment_first_200": treatment[:200],
            }
        )
        print(f"  control:    {control[:80]!r}…")
        print(f"  treatment:  {treatment[:80]!r}…\n")

    summary_path = args.out_dir / "summary.json"
    summary_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report → {summary_path}")
    print(
        "\nNow manually inspect each task's control vs treatment file pair and "
        "judge whether the treatment more clearly hits the evaluation hint."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
