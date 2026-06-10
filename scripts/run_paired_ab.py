#!/usr/bin/env python3
"""Server-PID-bound paired A/B orchestrator.

docs/know-how下一步建议.md §4.3 — every result must be tagged with the
``server_pid`` it ran against so that paired comparisons can be sliced by
that key. Cross-PID comparisons are explicitly exploratory and don't enter
the main paired-statistics path.

This script is a thin orchestrator on top of the existing per-seed
``verify_frontier_eng.py`` + ``judge_frontier_eng.py`` pipeline. It adds:

  * pre-run capture of the how server's ``/healthz`` and PID (via lsof)
  * post-run sha256 of every ``*.control.txt`` / ``*.treatment.txt`` file
    so the (server_pid, seed, task, request_hash, response_hash) tuple
    becomes the durable identity of each call
  * a top-level ``harness_meta.json`` recording start/end timestamps,
    bundle pointers, and any server-PID changes detected mid-run
  * a manifest that links the run to a frozen bundle (or warns if none)

Usage::

    python scripts/run_paired_ab.py \\
      --label p0_smoke_n2 \\
      --how-base http://127.0.0.1:8088 \\
      --seeds 1 2 \\
      --bundle-label iter4_d07ddac \\
      --temperature 0.3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know import config  # noqa: E402

FROZEN_ROOT = config.DATA_DIR / "frozen"
HARNESS_ROOT = config.BENCHMARKS_DIR / "paired_ab"


def _capture_server_meta(how_base: str, api_key: str) -> dict[str, Any]:
    """Snapshot the how server's identity right before the run starts."""
    import urllib.request

    out: dict[str, Any] = {"how_base": how_base}
    try:
        req = urllib.request.Request(
            f"{how_base.rstrip('/')}/healthz",
            headers={"X-API-Key": api_key},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            out["healthz"] = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        out["healthz_error"] = str(exc)

    # Resolve the listening process PID. /healthz doesn't expose it (and
    # shouldn't — that's a server-side leak we don't want). Get it from
    # lsof / ss locally.
    port = how_base.rsplit(":", 1)[-1].split("/")[0]
    try:
        pid_out = subprocess.check_output(
            ["bash", "-c", f"lsof -ti :{port} -sTCP:LISTEN 2>/dev/null | head -1"],
            text=True,
        ).strip()
        out["server_pid"] = int(pid_out) if pid_out.isdigit() else None
    except Exception as exc:  # noqa: BLE001
        out["server_pid_error"] = str(exc)
        out["server_pid"] = None

    # Capture the model endpoint identity without leaking the key.
    out["deepseek_base_url"] = config.DEEPSEEK_BASE_URL
    out["deepseek_muse_model"] = config.DEEPSEEK_MUSE_MODEL
    out["embedding_model"] = config.EMBEDDING_MODEL

    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_one_seed(seed: int, out_dir: Path, how_base: str, temperature: float) -> dict[str, Any]:
    """Run verify_frontier_eng.py for one seed; return per-task hashes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir.parent / f"seed_{seed}.verify.log"
    start = time.time()
    cmd = [
        sys.executable,
        "scripts/verify_frontier_eng.py",
        "--out-dir", str(out_dir),
        "--how-base", how_base,
        "--temperature", str(temperature),
        "--seed", str(seed),
    ]
    with open(log_path, "wb") as fh:
        rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=config.PROJECT_ROOT)
    duration = time.time() - start

    # Walk the output directory; sha256 every control/treatment file pair.
    per_task: list[dict[str, Any]] = []
    if out_dir.exists():
        ctl_files = sorted(out_dir.glob("*.control.txt"))
        for ctl in ctl_files:
            task_id = ctl.name[: -len(".control.txt")]
            trt = out_dir / f"{task_id}.treatment.txt"
            row: dict[str, Any] = {"task_id": task_id, "seed": seed}
            try:
                row["control_response_hash"] = _sha256_file(ctl)
                row["control_size"] = ctl.stat().st_size
            except FileNotFoundError:
                row["control_response_hash"] = None
            if trt.exists():
                row["treatment_response_hash"] = _sha256_file(trt)
                row["treatment_size"] = trt.stat().st_size
            else:
                row["treatment_response_hash"] = None
            per_task.append(row)

    return {
        "seed": seed,
        "return_code": rc,
        "duration_s": round(duration, 2),
        "task_count": len(per_task),
        "tasks": per_task,
    }


def _judge_one_seed(seed: int, out_dir: Path) -> dict[str, Any]:
    log_path = out_dir.parent / f"seed_{seed}.judge.log"
    cmd = [
        sys.executable,
        "scripts/judge_frontier_eng.py",
        "--report-dir", str(out_dir),
        "--seed", str(seed),
    ]
    start = time.time()
    with open(log_path, "wb") as fh:
        rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=config.PROJECT_ROOT)
    return {"seed": seed, "judge_return_code": rc, "judge_duration_s": round(time.time() - start, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="Run label (e.g. p0_smoke_n2)")
    ap.add_argument("--how-base", default=os.environ.get("ROSCLAW_HOW_BASE", "http://127.0.0.1:8088"))
    ap.add_argument(
        "--how-api-key", default=os.environ.get("ROSCLAW_HOW_API_KEY", "rw_sk_dev_local")
    )
    ap.add_argument("--seeds", nargs="+", type=int, required=True)
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument(
        "--bundle-label",
        default=None,
        help="Frozen bundle label this run should be tagged with (e.g. iter4_d07ddac).",
    )
    ap.add_argument(
        "--skip-judge", action="store_true", help="Run verify only; skip the judge stage."
    )
    args = ap.parse_args()

    run_root = HARNESS_ROOT / args.label
    if run_root.exists():
        raise SystemExit(
            f"[paired-ab] {run_root} already exists. Delete it or use a unique --label."
        )
    run_root.mkdir(parents=True)

    start_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. snapshot server meta at start
    pre_meta = _capture_server_meta(args.how_base, args.how_api_key)
    if pre_meta.get("server_pid") is None:
        print(f"[paired-ab] WARNING: could not resolve server PID for {args.how_base}")
    else:
        print(f"[paired-ab] server_pid={pre_meta['server_pid']} on {args.how_base}")

    # 2. record bundle pointer
    bundle_info: dict[str, Any] = {"label": args.bundle_label}
    if args.bundle_label:
        bdir = FROZEN_ROOT / args.bundle_label
        manifest_path = bdir / "bundle_manifest.json"
        if not manifest_path.exists():
            print(f"[paired-ab] WARNING: bundle '{args.bundle_label}' has no manifest at {manifest_path}")
        else:
            with open(manifest_path, encoding="utf-8") as fh:
                bundle_info["manifest"] = json.load(fh)
    else:
        print("[paired-ab] WARNING: no --bundle-label set; results are NOT reproducible.")

    # 3. run all seeds sequentially (paired integrity > wall-clock); aggressive
    # parallelism is the orchestrate_iter4_n30.sh path, not this one.
    per_seed: list[dict[str, Any]] = []
    for s in args.seeds:
        seed_dir = run_root / f"seed_{s}"
        print(f"[paired-ab] seed={s} → {seed_dir}")
        per_seed.append(_run_one_seed(s, seed_dir, args.how_base, args.temperature))
        if not args.skip_judge:
            per_seed[-1].update(_judge_one_seed(s, seed_dir))

    # 4. snapshot server meta again at end so we can detect PID changes
    post_meta = _capture_server_meta(args.how_base, args.how_api_key)
    pid_drifted = (
        pre_meta.get("server_pid") != post_meta.get("server_pid")
        and pre_meta.get("server_pid") is not None
    )
    if pid_drifted:
        print(
            f"[paired-ab] WARNING: server_pid changed mid-run "
            f"({pre_meta.get('server_pid')} → {post_meta.get('server_pid')})."
        )

    end_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    harness_meta = {
        "schema_version": 1,
        "label": args.label,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "seeds": list(args.seeds),
        "temperature": args.temperature,
        "how_base": args.how_base,
        "pre_meta": pre_meta,
        "post_meta": post_meta,
        "server_pid_drifted_mid_run": pid_drifted,
        "bundle": bundle_info,
        "per_seed": per_seed,
    }
    (run_root / "harness_meta.json").write_text(
        json.dumps(harness_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[paired-ab] done. meta → {run_root}/harness_meta.json")
    if pid_drifted:
        print("[paired-ab] EXIT 2 due to PID drift — results NOT entered into paired stats.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
