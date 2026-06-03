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

# Same set as in the Phase 1 doc — these symptoms exercise the cross-domain
# analogies we expect bridge_index.json to provide.
BENCHMARK_SUITE = [
    {
        "task_id": "ERR_001_PID_runaway",
        "symptom": (
            "PID controller drives a robotic-arm joint into sustained oscillation when "
            "the integral term saturates. Latency from sensor to actuator is 30 ms."
        ),
        "evaluation_hint": "should mention anti-windup clamp / derivative-on-measurement / gain scheduling",
    },
    {
        "task_id": "ERR_002_CUDA_OOM",
        "symptom": (
            "A vision-language-navigation model's KV-cache grows linearly with "
            "trajectory length, causing CUDA OOM after ~800 steps."
        ),
        "evaluation_hint": "should propose sliding-window/circular-buffer KV-cache truncation",
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
