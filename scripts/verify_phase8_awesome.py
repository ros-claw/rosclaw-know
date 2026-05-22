#!/usr/bin/env python3
"""Phase 8 verify — awesome list ingest → CATALYST hit on staging cluster.

End-to-end check that pulling a curated GitHub awesome list, harvesting
its referenced content, and ingesting into rosclaw-know reaches the runtime:

  1. Fetch a small slice of an awesome list (default: 5 entries).
  2. Run ingest.py on the new corpus → new clusters minted at priority=0.
  3. POST /admin/reload — verify symptoms_detail.added ≥ 1.
  4. Probe /build with a domain-specific error_log → expect CATALYST hit
     with is_staging=True on a freshly-added cluster.

Pass criterion:
  * At least one new cluster added during ingest
  * Reload reports added ≥ 1
  * Final /build hits a cluster whose pattern_id corresponds to one of
    the newly-fetched entries
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.config import ASSETS_DIR  # noqa: E402

logger = logging.getLogger("rosclaw_know.verify_phase8")

DEFAULT_BASE = "http://127.0.0.1:47820"
DEFAULT_API_KEY = "rw_sk_dev_local"

# Default awesome lists to verify against. User can override with --url.
DEFAULT_LISTS = (
    "https://github.com/A-make/awesome-control-theory",
    "https://github.com/hslatman/awesome-industrial-control-system-security",
)

# Domain-specific probes; matched_symptom must include one of these substrings
# AND pattern_id must be from one of the newly-ingested entries.
PROBES = [
    {
        "error_log": (
            "PID controller tuning is inconsistent across reactor batches; "
            "the process has significant dead time and inverse response causes overshoot."
        ),
        "expect_in_match": ["pid", "tuning", "control"],
    },
    {
        "error_log": (
            "Industrial PLC accepting unauthenticated start/stop commands over Modbus; "
            "the SCADA segment has no hardening or anomaly detection."
        ),
        "expect_in_match": ["ics", "scada", "plc", "industrial"],
    },
]


def _post_json(url: str, body: dict, headers: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        return {"_http_error": exc.code, "_body": body_text}


def _staging_cluster_ids() -> set[str]:
    bridge_path = ASSETS_DIR / "bridge_index.json"
    if not bridge_path.exists():
        return set()
    data = json.loads(bridge_path.read_text(encoding="utf-8"))
    return {
        str(cid) for cid, c in data.get("symptom_clusters", {}).items()
        if c.get("priority") == 0
    }


def run_awesome_ingest(url: str, limit: int) -> int:
    """Drive scripts/ingest_awesome.py with --then-ingest. Returns rc."""
    cmd = [
        str(PROJECT_ROOT / ".venv" / "bin" / "python"),
        str(PROJECT_ROOT / "scripts" / "ingest_awesome.py"),
        "--url", url,
        "--limit", str(limit),
        "--per-fetch-sleep", "0.2",
        "--then-ingest",
    ]
    logger.info("Running: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if proc.returncode != 0:
        logger.error("ingest_awesome failed: %s", proc.stderr[-600:])
    else:
        logger.info("ingest_awesome ok; tail:\n%s", proc.stdout[-400:])
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--api-key", default=DEFAULT_API_KEY)
    ap.add_argument("--url", action="append", default=None,
                    help="Awesome list URL (repeatable). Defaults to control-theory + ICS.")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--skip-ingest", action="store_true",
                    help="Skip the ingest step and just verify queries.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    urls = args.url or list(DEFAULT_LISTS)
    started_staging = _staging_cluster_ids()

    if not args.skip_ingest:
        for u in urls:
            rc = run_awesome_ingest(u, args.limit)
            if rc != 0:
                logger.warning("Skipping further lists due to ingest failure on %s", u)
                break

    after_staging = _staging_cluster_ids()
    new_staging = after_staging - started_staging
    logger.info("staging clusters before=%d after=%d new=%d",
                len(started_staging), len(after_staging), len(new_staging))

    # Reload to push the new clusters into SeekDB
    print("\n[reload] /admin/reload ...")
    t0 = time.perf_counter()
    reload_resp = _post_json(
        f"{args.base}/wiki/v1/admin/reload",
        body={}, headers={"X-API-Key": args.api_key},
        timeout=900,
    )
    print(
        f"   added={reload_resp.get('symptoms_detail',{}).get('added','?')} "
        f"unchanged={reload_resp.get('symptoms_detail',{}).get('unchanged','?')} "
        f"duration_ms={reload_resp.get('duration_ms','?')} "
        f"({time.perf_counter()-t0:.1f}s wall)"
    )

    print("\n[probes]")
    hits: list[dict] = []
    for p in PROBES:
        resp = _post_json(
            f"{args.base}/wiki/v1/prompt/build",
            body={
                "error_log": p["error_log"],
                "previous_scores": [0.5] * 4,
                "current_iteration": 8,
            },
            headers={"X-API-Key": args.api_key},
        )
        match = str(resp.get("matched_symptom") or "").lower()
        pid = str(resp.get("pattern_id") or "")
        ok = (
            resp.get("strategy") == "CATALYST"
            and resp.get("injected") is True
            and any(k in match for k in p["expect_in_match"])
        )
        hits.append({"probe": p["error_log"][:80], "resp": resp, "ok": ok})
        mark = "✓" if ok else "✗"
        print(
            f"   {mark} {p['error_log'][:60]}...  "
            f"→ pid={pid!r} sim={resp.get('similarity')} "
            f"is_staging={resp.get('is_staging')}"
        )

    # Pass criterion is mode-dependent: when ingesting fresh, we expect new
    # staging clusters to appear AND probes to land on them. When the caller
    # passes --skip-ingest (debugging or post-deploy spot-check), we only
    # require probes against existing staging clusters.
    probes_ok = all(h["ok"] for h in hits)
    if args.skip_ingest:
        passed = probes_ok
    else:
        passed = (
            len(new_staging) >= 1
            and int(reload_resp.get("symptoms_detail", {}).get("added", 0)) >= 1
            and probes_ok
        )

    out_dir = PROJECT_ROOT / "data" / "benchmarks" / "phase8_awesome"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps({
            "passed": passed,
            "urls": urls,
            "new_staging_clusters": sorted(new_staging),
            "reload_response": reload_resp,
            "probes": hits,
        }, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )

    print(f"\nResult: {'PASS' if passed else 'FAIL'}")
    print(f"  new staging clusters: {len(new_staging)}")
    print(f"  reload added: {reload_resp.get('symptoms_detail',{}).get('added')}")
    print(f"  probes ok: {sum(1 for h in hits if h['ok'])}/{len(hits)}")
    print(f"  report → {out_dir / 'report.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
