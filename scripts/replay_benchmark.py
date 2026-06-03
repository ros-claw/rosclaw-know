#!/usr/bin/env python3
"""Phase 4 joint replay benchmark — full feedback-loop integration test.

Drives ``N`` synthetic agent rollouts against a live rosclaw-how server:

  1. POST /wiki/v1/prompt/build (CATALYST iteration so SeekDB routes).
  2. Use the returned ``injection_id`` + ``matched_symptom`` to decide a
     simulated post-injection score: each pattern is randomly tagged "good"
     (uplift +0.15 ± 0.05) or "bad" (-0.10 ± 0.05).
  3. POST /wiki/v1/prompt/feedback with the synthesised post_score.
  4. After all rollouts finish: export → distill → reweight → check that
     the demoted set matches the seeded "bad" set within tolerance.

Pass criterion:
  - The "bad" patterns observed at least ``MIN_SAMPLE_SIZE`` times all show
    uplift_mean < 0 AND end up flagged ``is_demoted``.
  - The "good" patterns observed at least ``MIN_SAMPLE_SIZE`` times all show
    uplift_mean > 0 AND are NOT flagged.

This is the closed-loop guarantee: the feedback path doesn't get tricked by
small-sample noise (defended by MIN_SAMPLE_SIZE) and the reweighter only
demotes when every contributing pattern is clearly negative.

Usage:

    .venv/bin/python scripts/replay_benchmark.py \\
        --how-endpoint http://127.0.0.1:47820/wiki/v1 \\
        --api-key rw_sk_dev_local \\
        --rollouts 60 --seed 17
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.bridge_reweighter import reweight_bridge_index  # noqa: E402
from rosclaw_know.feedback_distill import (  # noqa: E402
    MIN_SAMPLE_SIZE,
    PatternMetric,
    distill,
    is_demoted,
)

logger = logging.getLogger("rosclaw_know.replay_benchmark")


SYMPTOM_PROMPTS = [
    "PID actuator integral wind-up keeps growing while torque clamps at 237 N·m",
    "KV cache memory exhausted on long horizon rollouts; CUDA OOM after 800 steps",
    "PPO training entropy crashes to zero; policy fixates on a degenerate action",
    "Velocity command diverges to infinity when the integrator has no limiter",
    "VLN agent ignores distant landmarks in long-horizon instruction",
    "Closed-loop tracking diverges at high latency; open-loop plan stale",
    "RPC retry storm after a partial outage; tight while-True retry loop",
    "Numerical instability — NaN appearing in policy network weights",
]


def _post_json(url: str, body: dict, headers: dict[str, str], timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {"_http_error": exc.code, "_body": body_text}
    except urllib.error.URLError as exc:
        return {"_url_error": str(exc.reason)}


def _classify_pattern_outcome(pattern_id: str, rng: random.Random, tag_map: dict[str, str]) -> tuple[str, float]:
    """Return (tag, simulated delta_score) for this pattern.

    Tags are sticky per pattern_id so a pattern that's "good" stays "good"
    across rollouts — that's how distillation can actually learn.
    """
    tag = tag_map.setdefault(pattern_id, rng.choice(["good", "bad"]))
    if tag == "good":
        return tag, rng.gauss(0.15, 0.05)
    return tag, rng.gauss(-0.10, 0.05)


def run_rollouts(
    how_base: str,
    api_key: str,
    n_rollouts: int,
    rng: random.Random,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Drive N synthetic rollouts. Returns (tag_map, outcome summary list)."""
    tag_map: dict[str, str] = {}
    rollouts: list[dict[str, Any]] = []

    build_url = f"{how_base.rstrip('/')}/prompt/build"
    feedback_url = f"{how_base.rstrip('/')}/prompt/feedback"
    headers = {"X-API-Key": api_key}

    skipped_no_id = 0
    skipped_not_inject = 0
    for i in range(n_rollouts):
        symptom = rng.choice(SYMPTOM_PROMPTS)
        pre_score = round(rng.uniform(0.30, 0.60), 4)
        build_resp = _post_json(
            build_url,
            {
                "error_log": symptom,
                "previous_scores": [pre_score] * 4,
                "current_iteration": 8,  # plateau ⇒ CATALYST
            },
            headers,
        )
        if not build_resp.get("injected"):
            skipped_not_inject += 1
            continue

        injection_id = build_resp.get("injection_id")
        pattern_id = (
            build_resp.get("matched_pattern_id")
            or build_resp.get("pattern_id")
            # fall back: derive from matched_symptom if the server hasn't
            # added the pattern_id field yet
            or build_resp.get("matched_symptom")
        )
        if not injection_id or not pattern_id:
            skipped_no_id += 1
            continue

        tag, delta = _classify_pattern_outcome(pattern_id, rng, tag_map)
        post_score = max(0.0, min(1.0, pre_score + delta))

        fb_resp = _post_json(
            feedback_url,
            {
                "injection_id": injection_id,
                "post_score": round(post_score, 4),
                "iterations_to_resolve": rng.randint(1, 5),
                "agent_notes": f"replay rollout {i} tag={tag}",
            },
            headers,
        )
        rollouts.append({
            "injection_id": injection_id,
            "pattern_id": pattern_id,
            "tag": tag,
            "pre_score": pre_score,
            "post_score": round(post_score, 4),
            "delta": round(delta, 4),
            "feedback_status": fb_resp,
            "build_latency_ms": build_resp.get("latency_ms"),
        })

    if skipped_not_inject or skipped_no_id:
        logger.warning(
            "Skipped %d rollouts (not injected) and %d (missing injection_id/pattern_id)",
            skipped_not_inject, skipped_no_id,
        )
    return tag_map, rollouts


def _trigger_export(how_root: Path) -> Path | None:
    """Best-effort call into rosclaw-how's export script. Returns export-file path or None.

    Known caveat: SeekDB embedded is single-process. When the live server holds
    the datafile lock, an out-of-process ``export_outcomes.py`` cannot open it.
    The caller should fall back to ``_pull_stats_metrics`` in that case.
    """
    script = how_root / "scripts" / "export_outcomes.py"
    if not script.exists():
        logger.warning("export_outcomes.py not found at %s — skip", script)
        return None
    import subprocess  # noqa: PLC0415 — local import to keep top-level imports cheap

    cmd = [str(how_root / ".venv" / "bin" / "python"), str(script)]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.error("export_outcomes.py failed: %s", exc)
        return None
    logger.info("export_outcomes.py stdout: %s", out.stdout.strip()[-200:])
    exports = how_root / "data" / "exports"
    matches = sorted(exports.glob("outcomes-*.jsonl"))
    if matches and matches[-1].stat().st_size > 0:
        return matches[-1]
    logger.warning(
        "Export produced an empty file — likely SeekDB single-process lock. "
        "Falling back to /stats HTTP pull."
    )
    return None


def _pull_stats_metrics(how_base: str) -> dict[str, PatternMetric]:
    """HTTP fallback: build PatternMetric instances directly from ``/stats``.

    Used when ``export_outcomes.py`` cannot open SeekDB (single-process lock).
    The /stats endpoint already returns the aggregate per pattern_id, so we
    don't need a JSONL round-trip for evaluation.
    """
    url = f"{how_base.rstrip('/').rstrip('/wiki/v1')}/wiki/v1/stats"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        logger.error("Could not pull /stats: %s", exc)
        return {}

    metrics: dict[str, PatternMetric] = {}
    for pid, agg in payload.items():
        try:
            metrics[pid] = PatternMetric(
                pattern_id=pid,
                n=int(agg.get("n", 0)),
                uplift_mean=round(float(agg.get("avg_uplift", 0.0)), 4),
                uplift_std=0.0,  # /stats doesn't surface stdev today
                win_rate=round(float(agg.get("win_rate", 0.0)), 4),
                last_seen=str(agg.get("last_seen_iso") or ""),
            )
        except (TypeError, ValueError) as exc:
            logger.warning("Skipping malformed /stats entry %s: %s", pid, exc)
    return metrics


def evaluate(
    tag_map: dict[str, str],
    metrics: dict[str, PatternMetric],
    how_root: Path,
) -> tuple[bool, dict[str, Any]]:
    """Given ``metrics`` (already sourced), reweight and check tag agreement.

    Passed iff every "good" pattern with n ≥ MIN_SAMPLE_SIZE has
    uplift_mean > 0 AND is NOT demoted, AND every "bad" pattern with
    n ≥ MIN_SAMPLE_SIZE has uplift_mean < 0 AND IS demoted.
    """
    # Persist metrics so the reweighter has something to chew on.
    from rosclaw_know.feedback_distill import write_metrics
    write_metrics(metrics)

    rew = reweight_bridge_index()

    passed = True
    per_pattern: dict[str, Any] = {}
    for pid, m in metrics.items():
        expected = tag_map.get(pid, "unknown")
        eligible = m.n >= MIN_SAMPLE_SIZE
        demoted = is_demoted(m)
        ok = True
        if eligible:
            if expected == "good":
                ok = (m.uplift_mean > 0) and (not demoted)
            elif expected == "bad":
                ok = (m.uplift_mean < 0) and demoted
        per_pattern[pid] = {
            "expected_tag": expected,
            "n": m.n,
            "uplift_mean": m.uplift_mean,
            "win_rate": m.win_rate,
            "demoted": demoted,
            "eligible": eligible,
            "ok": ok,
        }
        if eligible and not ok:
            passed = False
    return passed, {"reweight_stats": rew, "patterns": per_pattern}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--how-endpoint", default="http://127.0.0.1:47820/wiki/v1")
    ap.add_argument("--api-key", default=os.environ.get("ROSCLAW_HOW_API_KEY", "rw_sk_dev_local"))
    ap.add_argument("--rollouts", type=int, default=60)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument(
        "--how-root",
        type=Path,
        default=PROJECT_ROOT.parent / "rosclaw-how",
        help="Path to rosclaw-how repo root (for export_outcomes.py).",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    rng = random.Random(args.seed)

    logger.info("== Phase 4 joint replay benchmark ==  rollouts=%d seed=%d", args.rollouts, args.seed)
    t0 = time.perf_counter()
    tag_map, rollouts = run_rollouts(args.how_endpoint, args.api_key, args.rollouts, rng)
    logger.info("Drove %d successful rollouts in %.1fs", len(rollouts), time.perf_counter() - t0)
    if not rollouts:
        logger.error("No successful rollouts — abort. Is rosclaw-how with /prompt/feedback running?")
        return 1

    export_path = _trigger_export(args.how_root)
    metrics_source: str
    if export_path:
        logger.info("Exported outcomes → %s", export_path)
        metrics = distill(exports_dir=args.how_root / "data" / "exports")
        metrics_source = f"jsonl:{export_path.name}"
    else:
        logger.warning("Falling back to /stats HTTP for metrics (SeekDB single-process lock).")
        metrics = _pull_stats_metrics(args.how_endpoint)
        metrics_source = "http:/wiki/v1/stats"

    if not metrics:
        logger.error("No metrics from either source — abort.")
        return 1

    passed, report = evaluate(tag_map, metrics, args.how_root)
    out_dir = PROJECT_ROOT / "data" / "benchmarks" / "phase4_replay"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps({
            "passed": passed,
            "seed": args.seed,
            "rollouts_attempted": args.rollouts,
            "rollouts_landed": len(rollouts),
            "metrics_source": metrics_source,
            "tag_map": tag_map,
            "evaluation": report,
            "rollout_samples": rollouts[:10],
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Console summary
    print(f"\nResult: {'PASS' if passed else 'FAIL'}")
    eligible = [p for p, info in report["patterns"].items() if info["eligible"]]
    if eligible:
        delta_pos = [report["patterns"][p]["uplift_mean"] for p in eligible if report["patterns"][p]["expected_tag"] == "good"]
        delta_neg = [report["patterns"][p]["uplift_mean"] for p in eligible if report["patterns"][p]["expected_tag"] == "bad"]
        if delta_pos:
            print(f"  good  patterns (n≥{MIN_SAMPLE_SIZE}): mean uplift = {statistics.fmean(delta_pos):+.3f}  count={len(delta_pos)}")
        if delta_neg:
            print(f"  bad   patterns (n≥{MIN_SAMPLE_SIZE}): mean uplift = {statistics.fmean(delta_neg):+.3f}  count={len(delta_neg)}")
    print(f"  report → {out_dir / 'report.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
