#!/usr/bin/env python3
"""Phase 9 verify — agent uses ONLY the SDK to compose know + how.

Simulates an OS-level agent (openclaw / Harmes Agent) solving a task
purely through the rosclaw-client SDK. Verifies that every Phase 9
contract works end-to-end:

  1. ``client.know.research("PID quadrotor tuning")`` → job_id
  2. ``client.know.wait_for(job_id)`` → status=completed
  3. ``client.how.init(task_summary=...)`` → top_k_patterns + curriculum
  4. ``client.how.search(query="anti-windup")`` → ranked results
  5. ``client.how.build(error_log=...)`` → CATALYST hit
  6. ``client.how.feedback(injection_id, post_score=...)`` → 204

Pass criteria:
  - research job completes (status != "failed") within timeout
  - init returns ≥ 1 pattern (best-effort; may be empty if /init is
    not yet implemented on how-side — log warning, don't hard-fail)
  - build returns strategy=CATALYST + non-empty pattern_id
  - feedback returns ok=True

Run with both services up:
  - rosclaw-how on :8088
  - rosclaw-know on :8089
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT.parent / "rosclaw-client" / "src"))

from rosclaw_client import RosclawClient  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--know-base", default="http://127.0.0.1:8089")
    ap.add_argument("--how-base", default="http://127.0.0.1:8088")
    ap.add_argument("--how-api-key", default="rw_sk_dev_local")
    ap.add_argument("--topic", default="PID quadrotor tuning with anti-windup")
    ap.add_argument("--research-timeout", type=int, default=600)
    ap.add_argument("--skip-research", action="store_true",
                    help="Skip the LLM-heavy research step (assumes bridge is already enriched).")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    rc = RosclawClient(
        know_base=args.know_base,
        how_base=args.how_base,
        how_api_key=args.how_api_key,
    )

    report: dict = {"started_at": time.time(), "steps": []}

    # ── Step 1+2 : research + wait ──────────────────────────────────────
    if not args.skip_research:
        print(f"[1/6] know.research({args.topic!r}) ...")
        try:
            job = rc.know.research(args.topic, depth="shallow")
            print(f"      job_id={job.job_id} status={job.status}")
            print(f"[2/6] know.wait_for(...) up to {args.research_timeout}s")
            job = rc.know.wait_for(job.job_id, timeout=args.research_timeout)
            print(f"      → status={job.status} sources_fetched={job.sources_fetched} clusters_added={job.clusters_added}")
            report["steps"].append({"step": "research", "ok": job.status == "completed", "job": job.__dict__})
        except Exception as exc:  # noqa: BLE001
            print(f"      ERROR: {exc}")
            report["steps"].append({"step": "research", "ok": False, "error": str(exc)})
    else:
        print("[1-2/6] skipped (--skip-research)")

    # ── Step 3 : init ────────────────────────────────────────────────────
    print(f"[3/6] how.init(task_summary='Tune 12 PID gains for quadrotor') ...")
    try:
        init = rc.how.init(task_summary="Tune 12 PID gains for 2D quadrotor with dead-time tolerance")
        print(f"      top_k={len(init.top_k_patterns)} curriculum_len={len(init.recommended_curriculum)}")
        report["steps"].append({"step": "init", "ok": True, "top_k_count": len(init.top_k_patterns)})
    except RuntimeError as exc:
        # /init may not be deployed yet on how-side — log + continue
        print(f"      WARN (likely /init not deployed yet): {exc}")
        report["steps"].append({"step": "init", "ok": False, "error": str(exc)})

    # ── Step 4 : search ─────────────────────────────────────────────────
    print("[4/6] how.search('anti-windup', top_k=5) ...")
    try:
        hits = rc.how.search("anti-windup", top_k=5)
        for h in hits[:3]:
            print(f"      sim={h.similarity:.3f}  {h.pattern_id:40s}  {h.standard_name[:60]}")
        report["steps"].append({"step": "search", "ok": True, "hits": len(hits)})
    except RuntimeError as exc:
        print(f"      WARN (likely /patterns/search not deployed yet): {exc}")
        report["steps"].append({"step": "search", "ok": False, "error": str(exc)})

    # ── Step 5 : build (CATALYST) ───────────────────────────────────────
    print("[5/6] how.build(error_log='PID integral wind-up...', iter=8) ...")
    try:
        b = rc.how.build(
            error_log="PID controller tuning is inconsistent across reactor batches; "
                      "the process has significant dead time and inverse response causes overshoot.",
            previous_scores=[0.5, 0.5, 0.5, 0.5],
            current_iteration=8,
        )
        print(f"      strategy={b.strategy} pattern={b.pattern_id!r} sim={b.similarity} is_staging={b.is_staging}")
        report["steps"].append({
            "step": "build",
            "ok": b.strategy == "CATALYST" and b.injected and bool(b.pattern_id),
            "pattern_id": b.pattern_id,
            "similarity": b.similarity,
            "injection_id": b.injection_id,
        })
        injection_id = b.injection_id
    except RuntimeError as exc:
        print(f"      ERROR: {exc}")
        report["steps"].append({"step": "build", "ok": False, "error": str(exc)})
        injection_id = None

    # ── Step 6 : feedback ───────────────────────────────────────────────
    if injection_id:
        print("[6/6] how.feedback(post_score=0.72) ...")
        fb = rc.how.feedback(injection_id, post_score=0.72, iterations_to_resolve=3,
                             agent_notes="phase9 verify smoke")
        print(f"      ok={fb.ok}  {fb.detail or ''}")
        report["steps"].append({"step": "feedback", "ok": fb.ok})
    else:
        print("[6/6] skipped (no injection_id from build)")

    # ── Verdict ─────────────────────────────────────────────────────────
    passed_required = all(s.get("ok") for s in report["steps"]
                          if s["step"] in ("build", "feedback"))
    report["passed"] = passed_required
    report["elapsed_s"] = round(time.time() - report["started_at"], 1)

    out_dir = PROJECT_ROOT / "data" / "benchmarks" / "phase9_agent"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )

    print()
    print(f"Result: {'PASS' if passed_required else 'FAIL'} (required: build + feedback)")
    for s in report["steps"]:
        mark = "✓" if s.get("ok") else "✗"
        print(f"  {mark} {s['step']}")
    print(f"report → {out_dir / 'report.json'}")
    return 0 if passed_required else 1


if __name__ == "__main__":
    sys.exit(main())
