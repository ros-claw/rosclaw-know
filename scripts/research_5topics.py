#!/usr/bin/env python3
"""One-off orchestrator: fetch corpus for the 5 sim<floor benchmark topics,
ingest each through the now-QC-enabled muse, /admin/reload how at the end.

The 5 topics are picked to fill the bridge coverage gaps surfaced by the
verify_frontier_eng 4-cell A/B: TASK_004 (rare event simulation), TASK_005
(AES software perf), TASK_007 (Li-ion fast charging), TASK_009 (topology
optimization), TASK_010 (UAV motion blur).

Serial by design — each ingest mutates bridge_index.json, parallel runs
would race the write.  Total wall time ~25-40 min depending on muse work.
"""
from __future__ import annotations

import asyncio
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rosclaw_know import config  # noqa: E402  triggers .env load
from rosclaw_know.incremental_pipeline import run_incremental_ingest  # noqa: E402
from rosclaw_know.research_sources import collect_sources  # noqa: E402

TOPICS: list[tuple[str, str]] = [
    (
        "rare_event_simulation",
        "rare event Monte Carlo simulation importance sampling subset "
        "simulation cross-entropy method splitting RESTART reliability",
    ),
    (
        "aes_software_perf",
        "AES-128 software performance vectorization AES-NI bitslicing "
        "intrinsics throughput GCM CTR PCLMULQDQ",
    ),
    (
        "liion_fast_charging",
        "lithium-ion fast charging CC-CV pulse charging lithium plating "
        "prevention battery capacity fade SOC current taper",
    ),
    (
        "topology_optimization",
        "topology optimization SIMP density filter sensitivity filter "
        "Helmholtz PDE filter Heaviside projection mesh independence",
    ),
    (
        "motion_blur_uav",
        "motion blur deblurring Wiener filter blind deconvolution UAV "
        "inspection IMU-aided global shutter rolling shutter",
    ),
]

HOW_RELOAD_URL = "http://127.0.0.1:47820/wiki/v1/admin/reload"
HOW_API_KEY = "rw_sk_dev_local"


async def _ingest_topic(slug: str, topic: str) -> dict:
    corpus_dir = config.DATA_DIR / "research_corpus" / slug
    corpus_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()

    sources = await collect_sources(topic, depth="shallow", budget_tokens=50_000)
    print(f"  fetched={len(sources)}  elapsed_fetch={time.perf_counter() - t0:.1f}s",
          flush=True)
    if not sources:
        return {"slug": slug, "fetched": 0, "muse_summary": None}

    for src in sources:
        (corpus_dir / src.filename).write_text(src.markdown_body, encoding="utf-8")

    t_ingest_start = time.perf_counter()
    summary = await run_incremental_ingest([corpus_dir])
    print(f"  ingest_elapsed={time.perf_counter() - t_ingest_start:.1f}s  "
          f"muse_summary={summary.get('muse', {})}",
          flush=True)
    return {"slug": slug, "fetched": len(sources), "muse_summary": summary.get("muse")}


def _reload_how() -> str:
    req = urllib.request.Request(
        HOW_RELOAD_URL,
        data=b"{}",
        headers={"Content-Type": "application/json", "X-API-Key": HOW_API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        return f"reload failed: {exc}"


async def main() -> None:
    results: list[dict] = []
    t_total = time.perf_counter()
    for slug, topic in TOPICS:
        print(f"\n=== {slug} === topic={topic!r}", flush=True)
        try:
            res = await _ingest_topic(slug, topic)
            results.append(res)
        except Exception as exc:  # noqa: BLE001
            print(f"  CRASH: {exc}", flush=True)
            results.append({"slug": slug, "error": str(exc)})
    print(f"\nTotal elapsed: {time.perf_counter() - t_total:.1f}s", flush=True)

    print("\n=== Reload how ===", flush=True)
    print(_reload_how())

    print("\n=== Summary ===", flush=True)
    for r in results:
        print(r)


if __name__ == "__main__":
    asyncio.run(main())
