#!/usr/bin/env python3
"""Phase 7 joint verification — active learning + staging lifecycle.

End-to-end test of the full self-improving knowledge loop:

  1. Pre-seed: feed a brand-new symptom (NOT in any cluster) repeatedly to
     /build until /wiki/v1/blind_spots reflects the gap.
  2. Active learning: run scripts/autodraft.py → DeepSeek drafts a synthetic
     markdown filling the gap. Auto-ingest writes it into bridge_index as
     a new cluster with ``priority=0`` (staging).
  3. Reload: /admin/reload picks up the new cluster.
  4. /build for the same symptom should now CATALYST-hit the staging
     cluster AND return ``is_staging=true``.
  5. Feedback: simulate 5 positive feedback events (delta_score > +0.05).
  6. Promote: scripts/promote.py finds the staging cluster, calls
     /admin/promote — priority → +1 (production).
  7. Final /build should hit the (now-production) cluster with
     ``is_staging`` falsy.

Pass criterion: each of (1)–(7) hits its expected state.

Many of these depend on rosclaw_how endpoints that may not exist yet:
``/wiki/v1/blind_spots`` (#49) ✓, ``/admin/promote`` (#53) pending,
``is_staging`` field on /build response (#54) pending. The verify
script reports each step independently so partial-readiness is visible.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.config import ASSETS_DIR  # noqa: E402

logger = logging.getLogger("rosclaw_know.verify_phase7")

DEFAULT_BASE = "http://127.0.0.1:8088"
DEFAULT_API_KEY = "rw_sk_dev_local"

# Brand-new symptom space — must NOT match anything currently in bridge.
BLIND_SPOT_ERROR_LOG = (
    "Quantum simulator state vector decoherence accumulates across noisy "
    "gate sequences; QAOA convergence degrades after layer depth 20 even "
    "with error mitigation. Trotter step size adaptation does not help."
)


def _post_json(url: str, body: dict, headers: dict[str, str], timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body_text = ""
        return {"_http_error": exc.code, "_body": body_text}


def _get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def step1_seed_blind_spot(base: str, api_key: str, repeats: int = 6) -> dict:
    """Hit /build with the new symptom multiple times to populate blind_spots."""
    url = f"{base}/wiki/v1/prompt/build"
    headers = {"X-API-Key": api_key}
    body = {
        "error_log": BLIND_SPOT_ERROR_LOG,
        "previous_scores": [0.5] * 4,
        "current_iteration": 8,
    }
    responses = []
    for _ in range(repeats):
        responses.append(_post_json(url, body, headers))
    return {"hits": repeats, "last_response": responses[-1] if responses else {}}


def step2_inspect_blind_spots(base: str) -> dict:
    try:
        payload = _get_json(f"{base}/wiki/v1/blind_spots")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "payload": payload}


def step3_autodraft(project_root: Path) -> dict:
    venv_py = project_root / ".venv" / "bin" / "python"
    cmd = [str(venv_py), str(project_root / "scripts" / "autodraft.py"), "--then-ingest"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    return {
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-800:],
        "stderr_tail": proc.stderr[-400:],
    }


def step4_reload(base: str, api_key: str) -> dict:
    return _post_json(
        f"{base}/wiki/v1/admin/reload",
        body={}, headers={"X-API-Key": api_key},
        timeout=600,
    )


def step5_build_after_draft(base: str, api_key: str) -> dict:
    return _post_json(
        f"{base}/wiki/v1/prompt/build",
        body={
            "error_log": BLIND_SPOT_ERROR_LOG,
            "previous_scores": [0.5] * 4,
            "current_iteration": 8,
        },
        headers={"X-API-Key": api_key},
    )


def step6_simulate_positive_feedback(
    base: str, api_key: str, n_episodes: int = 5
) -> dict:
    """Drive N successful injections + positive feedbacks for the staging cluster."""
    build_url = f"{base}/wiki/v1/prompt/build"
    fb_url = f"{base}/wiki/v1/prompt/feedback"
    headers = {"X-API-Key": api_key}
    body = {
        "error_log": BLIND_SPOT_ERROR_LOG,
        "previous_scores": [0.5] * 4,
        "current_iteration": 8,
    }
    ok = 0
    pattern_ids = set()
    for _ in range(n_episodes):
        b = _post_json(build_url, body, headers)
        iid = b.get("injection_id")
        pid = b.get("pattern_id")
        if not iid:
            continue
        if pid:
            pattern_ids.add(pid)
        _post_json(
            fb_url,
            {"injection_id": iid, "post_score": 0.85, "iterations_to_resolve": 2},
            headers,
        )
        ok += 1
    return {"feedbacks_ok": ok, "pattern_ids": sorted(pattern_ids)}


def step7_promote(project_root: Path, base: str, api_key: str) -> dict:
    """Run scripts/promote.py --apply."""
    venv_py = project_root / ".venv" / "bin" / "python"
    cmd = [
        str(venv_py),
        str(project_root / "scripts" / "promote.py"),
        "--base", base,
        "--api-key", api_key,
        "--apply",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return {
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-800:],
        "stderr_tail": proc.stderr[-400:],
    }


def step8_final_build(base: str, api_key: str) -> dict:
    return step5_build_after_draft(base, api_key)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--api-key", default=DEFAULT_API_KEY)
    ap.add_argument("--skip-autodraft", action="store_true",
                    help="Skip the LLM-heavy autodraft step (assumes cluster exists).")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    report: dict[str, Any] = {"started_at": time.time()}

    print("[1/8] Seeding blind-spot ...")
    report["seed"] = step1_seed_blind_spot(args.base, args.api_key, repeats=6)
    print(f"      hit /build x6  strategy={report['seed']['last_response'].get('strategy')!r}")

    print("[2/8] Inspecting /blind_spots ...")
    report["blind_spots"] = step2_inspect_blind_spots(args.base)
    print(f"      payload sample: {str(report['blind_spots'].get('payload'))[:200]}")

    if not args.skip_autodraft:
        print("[3/8] Running autodraft.py --then-ingest (LLM heavy)...")
        report["autodraft"] = step3_autodraft(PROJECT_ROOT)
        print(f"      autodraft rc={report['autodraft']['returncode']}")
    else:
        print("[3/8] Skipped autodraft (--skip-autodraft).")

    print("[4/8] /admin/reload ...")
    report["reload"] = step4_reload(args.base, args.api_key)
    print(f"      reload: {report['reload']}")

    print("[5/8] /build after draft ...")
    report["build_after_draft"] = step5_build_after_draft(args.base, args.api_key)
    bad = report["build_after_draft"]
    print(
        f"      strategy={bad.get('strategy')!r} "
        f"is_staging={bad.get('is_staging')} "
        f"pattern_id={bad.get('pattern_id')!r} "
        f"sim={bad.get('similarity')}"
    )

    print("[6/8] Simulating 5 positive feedbacks ...")
    report["feedback"] = step6_simulate_positive_feedback(args.base, args.api_key)
    print(f"      feedbacks_ok={report['feedback']['feedbacks_ok']} pids={report['feedback']['pattern_ids']}")

    print("[7/8] Running promote.py --apply ...")
    report["promote"] = step7_promote(PROJECT_ROOT, args.base, args.api_key)
    print(f"      promote rc={report['promote']['returncode']}")

    # Trigger a second reload so the priority change is loaded
    print("[7b]  /admin/reload (pick up new priorities)...")
    report["reload_2"] = step4_reload(args.base, args.api_key)

    print("[8/8] Final /build (should be production, is_staging falsy) ...")
    report["final_build"] = step8_final_build(args.base, args.api_key)
    fb = report["final_build"]
    print(
        f"      strategy={fb.get('strategy')!r} "
        f"is_staging={fb.get('is_staging')} "
        f"pattern_id={fb.get('pattern_id')!r}"
    )

    # Pass criteria — each step independent, partial readiness reported
    checks = {
        "blind_spots_reachable": bool(report["blind_spots"].get("ok")),
        "build_after_draft_catalyst": (
            report["build_after_draft"].get("strategy") == "CATALYST"
        ),
        "build_after_draft_is_staging": bool(report["build_after_draft"].get("is_staging")),
        "feedbacks_landed": report["feedback"]["feedbacks_ok"] >= 5,
        "promote_clean_rc": report["promote"]["returncode"] == 0,
        "final_is_production": (
            report["final_build"].get("strategy") == "CATALYST"
            and not report["final_build"].get("is_staging")
        ),
    }
    report["checks"] = checks
    passed = all(checks.values())
    report["passed"] = passed

    out_dir = PROJECT_ROOT / "data" / "benchmarks" / "phase7_active"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )

    print()
    print(f"Result: {'PASS' if passed else 'FAIL'}")
    for name, ok in checks.items():
        print(f"  {'✓' if ok else '✗'} {name}")
    print(f"  report → {out_dir / 'report.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
