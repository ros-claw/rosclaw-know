#!/usr/bin/env python3
"""Sprint 6: generate a seed `evidence_traces_seed.jsonl` with a
4-arm A/B fingerprint for two compiled patterns.

We need synthetic-but-realistic traces in CI so the evidence_distill
pipeline has something to chew on, and so the §Sprint 6 acceptance
gates ("80% of CATALYST traces have post_score_3+5", etc.) are
demonstrably enforced.

The generator is deterministic — same seed → same file — so a `git diff`
on the JSONL only shows up when this script changes.
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from rosclaw_know import config
from rosclaw_know.evidence_writer import EvidenceTraceWriter
from rosclaw_know.schemas import EvidenceTrace

# Effect sizes per arm — picked to give a clear placebo-adjusted uplift
# (~+0.13 for anti-windup, ~+0.15 for vectorize) without crossing into
# implausible-magnitude territory.
ARM_EFFECT: dict[str, tuple[float, float]] = {
    # arm → (mean uplift, std)
    "baseline": (0.000, 0.010),
    "true":     (0.150, 0.025),
    "placebo":  (0.020, 0.015),
    "shuffled": (0.010, 0.015),
}

# Patterns to simulate.  Each pattern picks a (task, embodiment, hint
# feature vocabulary) tuple.
SCENARIOS = [
    {
        "pattern_id": "compiled_zero_integral_gain_on_saturation",
        "task_name": "PIDTuning",
        "objective_direction": "maximize",
        "code_diff_phrases": [
            "set Ki_z to zero on saturation",
            "added anti windup back calculation",
            "clamp the integral term to zero",
        ],
    },
    {
        "pattern_id": "compiled_vectorize_inner_loop",
        "task_name": "KernelOpt",
        "objective_direction": "maximize",
        "code_diff_phrases": [
            "vectorise the inner loop with numpy broadcast",
            "replaced python loop with triton kernel",
            "added shared memory tiling",
        ],
    },
]

CATALYST_ARMS = ("true", "placebo", "shuffled")
SAMPLES_PER_ARM = 6


def _sample_pre_score(rng: random.Random, scenario_idx: int) -> float:
    """Anchor pre-score per scenario to keep the seed visually coherent."""
    base = 0.45 + 0.05 * scenario_idx
    return round(base + rng.gauss(0, 0.02), 4)


def _sample_post_chain(
    rng: random.Random,
    pre_score: float,
    arm: str,
) -> tuple[float, float, float, float]:
    """Generate (post_1, post_3, post_5, best_delta_5) for one trial."""
    mean, std = ARM_EFFECT[arm]
    # post_score_5 is the eventual lift; post_score_1 and post_score_3
    # interpolate so the chain looks like an actual evolutionary curve.
    delta_5 = rng.gauss(mean, std)
    post_5 = pre_score + delta_5
    post_3 = pre_score + delta_5 * rng.uniform(0.55, 0.85)
    post_1 = pre_score + delta_5 * rng.uniform(0.20, 0.45)
    return (
        round(post_1, 4),
        round(post_3, 4),
        round(post_5, 4),
        round(delta_5, 4),
    )


def _emit_trace(
    *,
    rng: random.Random,
    scenario_idx: int,
    scenario: dict,
    arm: str,
    sample_idx: int,
) -> EvidenceTrace:
    pre = _sample_pre_score(rng, scenario_idx)
    p1, p3, p5, d5 = _sample_post_chain(rng, pre, arm)
    pattern = scenario["pattern_id"]
    is_catalyst = arm in CATALYST_ARMS
    used_hint = (arm == "true") and rng.random() < 0.75  # ~75% adoption
    diff_summary: list[str] = []
    # ~67% of true-arm traces carry a non-empty diff summary; 50% of
    # placebo+shuffled also carry one (so total CATALYST coverage clears
    # the 50% gate cleanly even with stochastic variation).
    diff_probability = 0.85 if arm == "true" else 0.6
    if is_catalyst and rng.random() < diff_probability:
        phrases = scenario["code_diff_phrases"]
        k = rng.randint(1, len(phrases))
        diff_summary = rng.sample(phrases, k=k)
    strategy = "CATALYST" if is_catalyst else "NONE"
    verifier_status = "valid" if rng.random() < 0.92 else "invalid"
    return EvidenceTrace(
        trace_id=f"trace_{pattern}_{arm}_{sample_idx}",
        run_id=f"run_{pattern}_seed{sample_idx}",
        task_name=scenario["task_name"],
        iteration=4,
        injection_id=(
            f"inj_{pattern}_{arm}_{sample_idx}" if is_catalyst else None
        ),
        pattern_id=pattern,
        strategy=strategy,
        pre_score=pre,
        post_score_1=p1,
        post_score_3=p3,
        post_score_5=p5,
        best_delta_5=d5,
        code_diff_summary=diff_summary,
        hint_features=(
            ["zero_integral", "anti_windup"]
            if pattern.endswith("on_saturation") and arm == "true"
            else ["vectorize"] if arm == "true" else []
        ),
        used_hint=used_hint if is_catalyst else False,
        verifier_status=verifier_status,
        objective_direction=scenario["objective_direction"],
        arm=arm,
        timestamp=f"2026-06-03T0{scenario_idx}:{sample_idx:02d}:00Z",
    )


def generate(out_path: Path, *, rng_seed: int = 42) -> int:
    rng = random.Random(rng_seed)
    n = 0
    with EvidenceTraceWriter(out_path) as w:
        for scen_idx, scen in enumerate(SCENARIOS):
            for arm in ("baseline", "true", "placebo", "shuffled"):
                for s in range(SAMPLES_PER_ARM):
                    w.append(
                        _emit_trace(
                            rng=rng,
                            scenario_idx=scen_idx,
                            scenario=scen,
                            arm=arm,
                            sample_idx=s,
                        )
                    )
                    n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Generate the Sprint-6 seed evidence-trace JSONL.",
    )
    default_out = config.PROJECT_ROOT / "data" / "exports" / "evidence_traces_seed.jsonl"
    p.add_argument(
        "--out",
        default=str(default_out),
        help="Output JSONL path.",
    )
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv if argv is not None else sys.argv[1:])
    # Truncate before appending so re-runs are idempotent.
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    n = generate(out_path, rng_seed=args.seed)
    print(f"wrote {n} traces to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
