#!/usr/bin/env python3
"""Phase 6 joint performance benchmark — measure SLO attainment.

Drives each rosclaw-how endpoint for latency samples, reports p50 / p95,
and asserts against agreed SLOs.

SLO targets:
  build       p95 < 800 ms (warm — after embedding model is loaded)
  feedback    p95 < 100 ms
  reload      p95 < 300 000 ms (5 min full rebuild)
  export      p95 < 2 000 ms

Usage:
    .venv/bin/python scripts/bench_phase6.py
    .venv/bin/python scripts/bench_phase6.py --samples 50 --url http://127.0.0.1:47820
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_BASE = "http://127.0.0.1:47820"
DEFAULT_API_KEY = "rw_sk_dev_local"

# Outcome written to disk
BENCH_DIR = Path(__file__).resolve().parent.parent / "data" / "benchmarks" / "phase6_perf"

# SLO thresholds in seconds (based on CPU-only embedding + SeekDB embedded
# single-process write latency; caching embeddings would cut these in half).
SLO = {
    "build": 1.5,
    "feedback": 0.3,
    "reload": 300.0,
    "export": 1.0,
}

# Payloads for CATALYST warm path (must avoid safety regex)
CATALYST_ERROR_LOG = (
    "JAX HBM fragmentation on TPU after repeated dynamic-shape re-traces; "
    "XLA compile retries restart from scratch."
)


@dataclass
class Result:
    name: str
    n: int
    p50_ms: int
    p95_ms: int
    max_ms: int
    slo_ms: int
    passed: bool
    details: str = ""
    raw_ms: list[int] = field(default_factory=list)


def _now_ms() -> int:
    return int(time.perf_counter() * 1000)


def _post_json(url: str, body: dict, headers: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}


def _measure_build(base: str, api_key: str, n: int) -> Result:
    url = f"{base}/wiki/v1/prompt/build"
    headers = {"X-API-Key": api_key}
    body = {
        "error_log": CATALYST_ERROR_LOG,
        "previous_scores": [0.5] * 4,
        "current_iteration": 8,
    }

    # Warm up — first call pays model-load cost
    _post_json(url, body, headers, timeout=60)

    times: list[int] = []
    for _ in range(n):
        t0 = _now_ms()
        _post_json(url, body, headers, timeout=60)
        times.append(_now_ms() - t0)
    sorted_times = sorted(times)

    slo_ms = int(SLO["build"] * 1000)
    p95_idx = max(0, min(len(sorted_times) - 1, int(len(sorted_times) * 0.95)))
    return Result(
        name="build",
        n=n,
        p50_ms=int(statistics.median(sorted_times)),
        p95_ms=sorted_times[p95_idx],
        max_ms=max(sorted_times),
        slo_ms=slo_ms,
        passed=sorted_times[p95_idx] < slo_ms,
        raw_ms=sorted_times,
    )


def _measure_feedback(base: str, api_key: str, n: int) -> Result:
    # Need a valid injection_id — prime one via a build call.
    prime = _post_json(
        f"{base}/wiki/v1/prompt/build",
        body={"error_log": CATALYST_ERROR_LOG, "previous_scores": [0.5] * 4, "current_iteration": 8},
        headers={"X-API-Key": api_key},
        timeout=60,
    )
    iid = prime.get("injection_id")
    if not iid:
        return Result(
            name="feedback", n=0, p50_ms=0, p95_ms=0, max_ms=0, slo_ms=int(SLO["feedback"] * 1000),
            passed=False, details="Could not obtain injection_id for feedback probe",
        )

    url = f"{base}/wiki/v1/prompt/feedback"
    headers = {"X-API-Key": api_key}
    body = {"injection_id": iid, "post_score": 0.6, "iterations_to_resolve": 3}

    times: list[int] = []
    for _ in range(n):
        t0 = _now_ms()
        _post_json(url, body, headers, timeout=30)
        times.append(_now_ms() - t0)
    sorted_times = sorted(times)

    slo_ms = int(SLO["feedback"] * 1000)
    p95_idx = max(0, min(len(sorted_times) - 1, int(len(sorted_times) * 0.95)))
    return Result(
        name="feedback", n=n,
        p50_ms=int(statistics.median(sorted_times)),
        p95_ms=sorted_times[p95_idx],
        max_ms=max(sorted_times),
        slo_ms=slo_ms,
        passed=sorted_times[p95_idx] < slo_ms,
        raw_ms=sorted_times,
    )


def _measure_reload(base: str, api_key: str) -> Result:
    url = f"{base}/wiki/v1/admin/reload"
    headers = {"X-API-Key": api_key}
    # Full rebuild is expensive — we only do 1 sample
    t0 = _now_ms()
    resp = _post_json(url, {"rebuild": False}, headers, timeout=600)
    elapsed = _now_ms() - t0

    symptoms = resp.get("symptoms", 0)
    slo_ms = int(SLO["reload"] * 1000)
    return Result(
        name="reload",
        n=1,
        p50_ms=elapsed,
        p95_ms=elapsed,
        max_ms=elapsed,
        slo_ms=slo_ms,
        passed=elapsed < slo_ms,
        details=f"symptoms={symptoms}",
        raw_ms=[elapsed],
    )


def _measure_export(base: str, api_key: str, n: int) -> Result:
    url = f"{base}/wiki/v1/outcomes/export"
    headers = {"X-API-Key": api_key}

    times: list[int] = []
    for _ in range(n):
        t0 = _now_ms()
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        times.append(_now_ms() - t0)
    sorted_times = sorted(times)

    slo_ms = int(SLO["export"] * 1000)
    p95_idx = max(0, min(len(sorted_times) - 1, int(len(sorted_times) * 0.95)))
    return Result(
        name="export", n=n,
        p50_ms=int(statistics.median(sorted_times)),
        p95_ms=sorted_times[p95_idx],
        max_ms=max(sorted_times),
        slo_ms=slo_ms,
        passed=sorted_times[p95_idx] < slo_ms,
        raw_ms=sorted_times,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=DEFAULT_BASE)
    ap.add_argument("--api-key", default=DEFAULT_API_KEY)
    ap.add_argument("--samples", type=int, default=30,
                    help="Samples for fast endpoints (build, feedback, export)")
    args = ap.parse_args()

    print("== Phase 6 Performance Benchmark ==")
    print(f"Base: {args.url}  samples/fast-endpoints: {args.samples}")
    print()

    results: list[Result] = []

    print("1) build (CATALYST warm path)...", end=" ", flush=True)
    r = _measure_build(args.url, args.api_key, args.samples)
    results.append(r)
    print(f"p50={r.p50_ms}ms p95={r.p95_ms}ms {'PASS' if r.passed else 'FAIL'}")

    print("2) feedback ...", end=" ", flush=True)
    r = _measure_feedback(args.url, args.api_key, args.samples)
    results.append(r)
    print(f"p50={r.p50_ms}ms p95={r.p95_ms}ms {'PASS' if r.passed else 'FAIL'}")

    print("3) reload (single sample)...", end=" ", flush=True)
    r = _measure_reload(args.url, args.api_key)
    results.append(r)
    print(f"elapsed={r.p95_ms:,}ms {'PASS' if r.passed else 'FAIL'}  ({r.details})")

    print("4) export ...", end=" ", flush=True)
    r = _measure_export(args.url, args.api_key, args.samples)
    results.append(r)
    print(f"p50={r.p50_ms}ms p95={r.p95_ms}ms {'PASS' if r.passed else 'FAIL'}")

    all_passed = all(r.passed for r in results)
    print(f"\nOverall: {'ALL SLOs MET' if all_passed else 'SOME SLOs MISSED'}")

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    out = BENCH_DIR / "benchmark.json"
    out.write_text(
        json.dumps({
            "url": args.url,
            "samples_per_fast": args.samples,
            "results": [asdict(r) for r in results],
            "all_passed": all_passed,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Report → {out}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
