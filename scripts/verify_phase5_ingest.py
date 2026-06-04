#!/usr/bin/env python3
"""Phase 5 joint verification — synthetic ingest + hot-reload round trip.

End-to-end check that the closed knowledge growth loop actually grows:

  1. Write a synthetic markdown about a brand-new symptom not present in
     the current bridge_index (e.g. "TPU memory fragmentation during XLA").
  2. Run ``scripts/ingest.py`` against it — harvester + weaver + Muse
     produce a new cluster.
  3. POST ``/wiki/v1/admin/reload`` to rosclaw-how so the new cluster is
     loaded into SeekDB without bouncing the server.
  4. POST ``/wiki/v1/prompt/build`` with a CATALYST-friendly payload whose
     error_log matches the new symptom. Expect ``matched_symptom`` to be
     the freshly-minted cluster.

Pass criterion:
  ‑ ingest reports added ≥ 1 cluster
  ‑ /admin/reload reports added ≥ 1
  ‑ /build returns ``injected=True`` AND ``matched_symptom`` referencing
    one of the new cluster's standard_names (best-effort substring match)

The script cleans up after itself — deletes its synthetic markdown when
``--cleanup`` is passed (default off so you can re-run to debug).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know.config import ASSETS_DIR, WIKI_DIR  # noqa: E402

logger = logging.getLogger("rosclaw_know.verify_phase5")

DEFAULT_HOW_BASE = "http://127.0.0.1:47820"
DEFAULT_HOW_API_KEY = "rw_sk_dev_local"

# A symptom that should NOT collide with anything currently in bridge_index.
NEW_SYMPTOM = "TPU memory fragmentation during XLA compilation"
# IMPORTANT: error_log must NOT trip rosclaw-how's safety regex
# (OOM / NaN / torque / oscillation etc.) or the strategy goes SAFETY and we
# never reach CATALYST. "fragmentation" + "TPU" + "HBM" + "XLA" is safe.
NEW_ERROR_LOG = (
    "JAX HBM fragmentation on TPU after repeated dynamic-shape re-traces; "
    "XLA compile retries restart from scratch and headline HBM utilisation "
    "is only ~60% even though all slots show occupied."
)
NEW_CLUSTER_ID_PREFIX = "tpu_xla_fragmentation_phase5_synthetic"

SYNTHETIC_MD = """# TPU HBM fragmentation in JAX XLA compilation

When a JAX program compiled via XLA targets the TPU, repeated re-traces
of dynamic-shape inputs can fragment the HBM pool. The compiler logs show
all slots as occupied even though resident tensors only use ~60% of the
device. Retrying with `jit` plus pre-allocated buffers and `xla_flags`
forcing a single trace shape avoids the fragmentation. Tuning
`XLA_TPU_BUFFER_PADDING_RATIO` upward also helps when the workload
naturally produces variable shapes.

Failed attempt: doubling the HBM via topology reshape — fragmentation
re-emerges as soon as long-tail shapes return. The fix has to be at
the trace boundary, not the capacity ceiling.

```python
# Anti-pattern: per-step re-trace fragments HBM
@jax.jit
def step(state, batch):
    return policy.apply(state, batch)

for batch in stream:           # variable shape per iteration
    state = step(state, batch)

# Fix: pad batches to a small set of canonical shapes
@jax.jit
def step(state, padded_batch):
    return policy.apply(state, padded_batch)

for batch in stream:
    padded = pad_to_bucket(batch, BUCKETS)  # one of 4 shapes
    state = step(state, padded)
```
"""


def _post_json(url: str, body: dict, headers: dict[str, str], timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        return {"_http_error": exc.code, "_body": body_text}


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def write_synthetic_md(target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    fp = target_dir / "tpu_xla_fragmentation_phase5_synthetic.md"
    fp.write_text(SYNTHETIC_MD, encoding="utf-8")
    return fp


def run_ingest(md_path: Path) -> dict:
    """Invoke scripts/ingest.py with the synthetic markdown."""
    cmd = [
        str(PROJECT_ROOT / ".venv" / "bin" / "python"),
        str(PROJECT_ROOT / "scripts" / "ingest.py"),
        str(md_path),
    ]
    logger.info("Running ingest: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    logger.info("ingest stdout (tail):\n%s", proc.stdout[-2000:])
    if proc.returncode != 0:
        logger.error("ingest stderr (tail):\n%s", proc.stderr[-2000:])
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def call_reload(how_base: str, api_key: str) -> dict:
    # The reload re-encodes every cluster (~300ms-1s each on CPU) and re-upserts
    # into SeekDB; on a 300+ cluster bridge this can take 5 minutes.
    return _post_json(
        f"{how_base.rstrip('/')}/wiki/v1/admin/reload",
        body={},
        headers={"X-API-Key": api_key},
        timeout=600,
    )


def call_build(how_base: str, api_key: str, error_log: str) -> dict:
    return _post_json(
        f"{how_base.rstrip('/')}/wiki/v1/prompt/build",
        body={
            "error_log": error_log,
            "previous_scores": [0.5, 0.5, 0.5, 0.5],
            "current_iteration": 8,
        },
        headers={"X-API-Key": api_key},
    )


def _bridge_index_has_new_cluster() -> tuple[bool, str | None]:
    """True iff the synthetic cluster id (from the .md stem) is in bridge_index.

    Substring matching on the standard_name is too loose — any pre-existing
    cluster mentioning "TPU" would false-positive. The cluster id is derived
    from the slugified markdown stem (see Muse's ``_id_to_slug``), so we look
    for that specifically.
    """
    bridge_path = ASSETS_DIR / "bridge_index.json"
    if not bridge_path.exists():
        return False, None
    data = json.loads(bridge_path.read_text(encoding="utf-8"))
    for cid, cluster in data.get("symptom_clusters", {}).items():
        if NEW_CLUSTER_ID_PREFIX in str(cid):
            return True, str(cluster.get("standard_name") or cid)
    return False, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--how-base", default=os.environ.get("ROSCLAW_HOW_BASE", DEFAULT_HOW_BASE))
    ap.add_argument("--api-key", default=os.environ.get("ROSCLAW_HOW_API_KEY", DEFAULT_HOW_API_KEY))
    ap.add_argument(
        "--md-target-dir",
        type=Path,
        # Fall back if WIKI_DIR is unset, missing, or a broken symlink.
        default=(WIKI_DIR / "phase5_ingest" if WIKI_DIR and WIKI_DIR.is_dir()
                 else PROJECT_ROOT / "data" / "incoming"),
    )
    ap.add_argument("--skip-ingest", action="store_true", help="Skip the ingest step (assumes already done).")
    ap.add_argument("--cleanup", action="store_true", help="Delete the synthetic markdown after.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    t0 = time.perf_counter()
    md_path = write_synthetic_md(args.md_target_dir)
    logger.info("Synthetic markdown at %s", md_path)

    if not args.skip_ingest:
        ingest_result = run_ingest(md_path)
        if ingest_result["returncode"] != 0:
            logger.error("ingest failed — abort.")
            return 1
    else:
        logger.info("Skipping ingest step (--skip-ingest).")

    has_new, name = _bridge_index_has_new_cluster()
    if not has_new:
        logger.error("After ingest, bridge_index has no '%s' cluster — abort.", NEW_CLUSTER_ID_PREFIX)
        return 1
    logger.info("New cluster present in bridge: %r", name)

    reload_resp = call_reload(args.how_base, args.api_key)
    if "_http_error" in reload_resp:
        logger.error("reload failed: %s", reload_resp)
        return 1
    logger.info("reload response: %s", reload_resp)

    post_healthz = _get_json(f"{args.how_base.rstrip('/')}/healthz")
    logger.info("post-reload healthz cluster_count=%s", post_healthz.get("cluster_count"))

    build_resp = call_build(args.how_base, args.api_key, NEW_ERROR_LOG)
    logger.info("build response keys=%s strategy=%s matched=%r sim=%s",
                list(build_resp.keys()), build_resp.get("strategy"),
                build_resp.get("matched_symptom"), build_resp.get("similarity"))

    # CATALYST + new cluster id match (not a substring on names).
    pid = str(build_resp.get("pattern_id") or "")
    new_hit = (
        build_resp.get("injected") is True
        and build_resp.get("strategy") == "CATALYST"
        and NEW_CLUSTER_ID_PREFIX in pid
    )

    if args.cleanup:
        try:
            md_path.unlink()
        except OSError:
            pass

    passed = (
        bool(has_new)
        and int(reload_resp.get("symptoms", 0)) >= 1
        and new_hit
    )
    out_dir = PROJECT_ROOT / "data" / "benchmarks" / "phase5_ingest"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_report.json").write_text(
        json.dumps({
            "passed": passed,
            "elapsed_s": round(time.perf_counter() - t0, 1),
            "new_cluster_name": name,
            "reload_response": reload_resp,
            "build_response": build_resp,
            "post_count": post_healthz.get("cluster_count"),
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nResult: {'PASS' if passed else 'FAIL'}")
    print(f"  bridge has new cluster ({NEW_CLUSTER_ID_PREFIX}): {has_new}  ({name!r})")
    print(f"  reload symptoms loaded:         {reload_resp.get('symptoms', '?')}")
    print(f"  reload demoted skipped:         {reload_resp.get('demoted_skipped', '?')}")
    print(f"  build strategy:                 {build_resp.get('strategy')}")
    print(f"  build pattern_id:               {build_resp.get('pattern_id')!r}")
    print(f"  build similarity:               {build_resp.get('similarity')}")
    print(f"  injected:                       {build_resp.get('injected')}")
    print(f"  report → {out_dir / 'verify_report.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
