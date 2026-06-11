#!/usr/bin/env python3
"""Verify HOW's live routing against the canonical routing panel.

Doc §6 P0 hard gate. POSTs each task's symptom query (verbatim mirror of
verify_frontier_eng.py BENCHMARK_SUITE) to HOW's /wiki/v1/prompt/build
and asserts the returned ``pattern_id`` / ``strategy`` matches the
panel's contract.

This is the NEW gate-A "Retrieval Correctness" hard standard:
LLM-judge PANEL Δ is no longer the sole ship criterion. Routing must
be correct BEFORE any paired_ab is allowed to start.

Usage::

    python scripts/verify_routing_panel.py \\
        --base http://127.0.0.1:47820 \\
        --panel data/panels/routing_panel.yaml \\
        --out data/reports/routing_iter4_p9.json

Exit codes:
  0  all tasks pass
  1  one or more tasks FAIL — paired_ab must NOT launch
  2  HOW unreachable / panel malformed

The paired_ab launcher (scripts/launch_paired_ab_302ai.sh) should run
this with --strict before bringing up verify_frontier_eng.py. A passing
panel run is a prerequisite for any iter5+ ship.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


@dataclass(frozen=True)
class PanelEntry:
    task_id: str
    query: str
    expected_pattern_any: tuple[str, ...]
    expected_strategy_any: tuple[str, ...]
    expected_safety_label_any: tuple[str, ...]
    collateral_protect: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class ProbeResult:
    task_id: str
    status: str  # "pass" | "fail" | "unreachable"
    strategy: str | None
    injected: bool | None
    pattern_id: str | None
    similarity: float | None
    matched_symptom: str | None
    safety_label: str | None
    expected_pattern_any: tuple[str, ...]
    expected_strategy_any: tuple[str, ...]
    expected_safety_label_any: tuple[str, ...]
    fail_reason: str | None
    latency_ms: float | None


def _load_panel(panel_path: Path) -> list[PanelEntry]:
    data = yaml.safe_load(panel_path.read_text(encoding="utf-8"))
    tasks_raw = data.get("tasks") or []
    out: list[PanelEntry] = []
    for entry in tasks_raw:
        out.append(
            PanelEntry(
                task_id=str(entry["task_id"]),
                query=str(entry["query"]).strip(),
                expected_pattern_any=tuple(entry.get("expected_pattern_any") or ()),
                expected_strategy_any=tuple(entry.get("expected_strategy_any") or ()),
                expected_safety_label_any=tuple(
                    entry.get("expected_safety_label_any") or ()
                ),
                collateral_protect=tuple(entry.get("collateral_protect") or ()),
                notes=str(entry.get("notes") or "").strip(),
            )
        )
    return out


def _probe(
    entry: PanelEntry,
    *,
    base: str,
    api_key: str,
    timeout: float = 15.0,
) -> ProbeResult:
    """POST one query to HOW /wiki/v1/prompt/build and classify."""
    body = json.dumps(
        {
            "error_log": entry.query,
            # Plateau scores + past-warmup iteration force CATALYST.
            "previous_scores": [0.10] * 5,
            "current_iteration": 5,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/wiki/v1/prompt/build",
        data=body,
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return ProbeResult(
            task_id=entry.task_id,
            status="unreachable",
            strategy=None,
            injected=None,
            pattern_id=None,
            similarity=None,
            matched_symptom=None,
            safety_label=None,
            expected_pattern_any=entry.expected_pattern_any,
            expected_strategy_any=entry.expected_strategy_any,
            expected_safety_label_any=entry.expected_safety_label_any,
            fail_reason=f"HTTP {exc.code}: {exc.reason}",
            latency_ms=None,
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            task_id=entry.task_id,
            status="unreachable",
            strategy=None,
            injected=None,
            pattern_id=None,
            similarity=None,
            matched_symptom=None,
            safety_label=None,
            expected_pattern_any=entry.expected_pattern_any,
            expected_strategy_any=entry.expected_strategy_any,
            expected_safety_label_any=entry.expected_safety_label_any,
            fail_reason=f"HOW unreachable: {exc}",
            latency_ms=None,
        )
    latency_ms = (time.perf_counter() - start) * 1000

    try:
        data = json.loads(raw)
    except ValueError:
        return ProbeResult(
            task_id=entry.task_id,
            status="unreachable",
            strategy=None,
            injected=None,
            pattern_id=None,
            similarity=None,
            matched_symptom=None,
            safety_label=None,
            expected_pattern_any=entry.expected_pattern_any,
            expected_strategy_any=entry.expected_strategy_any,
            expected_safety_label_any=entry.expected_safety_label_any,
            fail_reason=f"non-JSON response: {raw[:200]}",
            latency_ms=latency_ms,
        )

    strategy = data.get("strategy")
    pattern_id = data.get("pattern_id")
    similarity = data.get("similarity")
    matched_symptom = data.get("matched_symptom")
    injected = data.get("injected")
    # SAFETY path uses ``symptom`` to label the matched safety class
    # (e.g., "Numerical_Instability"). When the response is from the
    # SAFETY path, pattern_id is None — the safety label is the contract.
    safety_label = data.get("symptom") if strategy == "SAFETY" else None

    # Effective strategy: when injected=False the labeled strategy
    # (e.g., CATALYST) means "we entered the path but bailed because
    # nothing was above the similarity floor" — which IS no_inject in
    # contract terms. Treat as ABSTAIN equivalent for panel passes.
    effective_strategy = strategy
    if injected is False:
        effective_strategy = "no_inject"

    # Pass rules — see routing_panel.yaml footer.
    pass_via_pattern = (
        bool(entry.expected_pattern_any)
        and pattern_id is not None
        and pattern_id in entry.expected_pattern_any
    )
    pass_via_strategy = bool(entry.expected_strategy_any) and (
        (strategy is not None and strategy in entry.expected_strategy_any)
        or (effective_strategy is not None and effective_strategy in entry.expected_strategy_any)
    )
    pass_via_safety_label = (
        bool(entry.expected_safety_label_any)
        and strategy == "SAFETY"
        and injected is True
        and safety_label is not None
        and safety_label in entry.expected_safety_label_any
    )

    if pass_via_pattern or pass_via_strategy or pass_via_safety_label:
        return ProbeResult(
            task_id=entry.task_id,
            status="pass",
            strategy=strategy,
            injected=injected,
            pattern_id=pattern_id,
            similarity=similarity,
            matched_symptom=matched_symptom,
            safety_label=safety_label,
            expected_pattern_any=entry.expected_pattern_any,
            expected_strategy_any=entry.expected_strategy_any,
            expected_safety_label_any=entry.expected_safety_label_any,
            fail_reason=None,
            latency_ms=latency_ms,
        )

    if entry.expected_pattern_any:
        reason = (
            f"got pattern_id={pattern_id!r} (injected={injected}, "
            f"strategy={strategy!r}), expected one of "
            f"{list(entry.expected_pattern_any)}"
        )
    elif entry.expected_strategy_any:
        reason = (
            f"got strategy={strategy!r} injected={injected}, expected one of "
            f"{list(entry.expected_strategy_any)}"
        )
    else:
        reason = (
            f"got safety_label={safety_label!r}, expected one of "
            f"{list(entry.expected_safety_label_any)}"
        )
    return ProbeResult(
        task_id=entry.task_id,
        status="fail",
        strategy=strategy,
        injected=injected,
        pattern_id=pattern_id,
        similarity=similarity,
        matched_symptom=matched_symptom,
        safety_label=safety_label,
        expected_pattern_any=entry.expected_pattern_any,
        expected_strategy_any=entry.expected_strategy_any,
        expected_safety_label_any=entry.expected_safety_label_any,
        fail_reason=reason,
        latency_ms=latency_ms,
    )


def _check_health(base: str, *, strict: bool, timeout: float = 5.0) -> dict | None:
    """Hit /healthz and (when --strict) refuse if degraded.

    Doc §4.1 — HOW should expose router_backend / assets_loaded /
    missing_assets. We refuse to run the panel against a degraded HOW
    when strict mode is on. Even without strict mode, a non-ok status
    is reported in the JSON output for forensic.
    """
    try:
        with urllib.request.urlopen(
            f"{base.rstrip('/')}/healthz", timeout=timeout
        ) as resp:
            health = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        if strict:
            print(
                f"[error] HOW /healthz unreachable: {exc} — strict mode refuses",
                file=sys.stderr,
            )
            return None
        return {"status": "unreachable", "error": str(exc)}

    if strict:
        problems: list[str] = []
        if health.get("status") not in (None, "ok"):
            problems.append(f"status={health['status']!r}")
        backend = health.get("router_backend")
        if backend is not None and backend != "seekdb":
            problems.append(f"router_backend={backend!r} (need seekdb)")
        loaded = health.get("assets_loaded")
        if loaded is False:
            problems.append("assets_loaded=False")
        missing = health.get("missing_assets")
        if missing:
            problems.append(f"missing_assets={missing}")
        if problems:
            print(
                "[error] HOW /healthz degraded — refusing to run panel:\n  "
                + "\n  ".join(problems),
                file=sys.stderr,
            )
            return None
    return health


def _format_report(results: list[ProbeResult], how_base: str) -> int:
    """Stdout report + exit code."""
    print()
    print(
        f"{'task_id':<32} {'status':<8} {'strategy':<14} "
        f"{'pattern_id':<40} {'sim':>6}"
    )
    print("─" * 105)
    n_pass = 0
    n_fail = 0
    n_unreach = 0
    for r in results:
        sim_str = f"{r.similarity:.4f}" if isinstance(r.similarity, (int, float)) else "  —  "
        pattern = (r.pattern_id or "—")[:40]
        strategy = (r.strategy or "—")[:14]
        print(
            f"{r.task_id:<32} {r.status:<8} {strategy:<14} "
            f"{pattern:<40} {sim_str:>6}"
        )
        if r.status == "pass":
            n_pass += 1
        elif r.status == "fail":
            n_fail += 1
        elif r.status == "unreachable":
            n_unreach += 1
    print("─" * 105)
    print(f"@ HOW base = {how_base}")
    print(f"PASS={n_pass}   FAIL={n_fail}   UNREACHABLE={n_unreach}   total={len(results)}")
    if n_fail or n_unreach:
        print()
        print("Failures:")
        for r in results:
            if r.status != "pass":
                print(f"  {r.task_id:<32} → {r.fail_reason}")
        return 1 if n_unreach == 0 else 2
    print("ALL PASS — routing panel cleared, paired_ab may launch.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--base",
        default=os.environ.get("ROSCLAW_HOW_BASE", "http://127.0.0.1:8088"),
        help=(
            "HOW server base URL (default: env ROSCLAW_HOW_BASE or "
            ":8088 — the post-2026-06-11 P0-W1 HOW port; previously "
            ":47820)."
        ),
    )
    ap.add_argument(
        "--panel",
        type=Path,
        default=PROJECT_ROOT / "data" / "panels" / "routing_panel.yaml",
        help="Path to routing_panel.yaml.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Optional output JSON path. If set, writes a full machine-"
            "readable report next to the human report on stdout."
        ),
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Refuse to run the panel when HOW /healthz is unreachable or "
            "reports router_backend != seekdb / assets_loaded false / "
            "missing_assets non-empty. paired_ab launcher should pass "
            "--strict so dead-code routing backends can't pass the gate."
        ),
    )
    ap.add_argument(
        "--api-key",
        default=os.environ.get("ROSCLAW_HOW_API_KEY", "rw_sk_dev_local"),
        help=(
            "API key for X-API-Key header. Default: env ROSCLAW_HOW_API_KEY "
            "or rw_sk_dev_local (HOW dev default)."
        ),
    )
    args = ap.parse_args()

    if not args.panel.exists():
        print(f"[error] panel file not found: {args.panel}", file=sys.stderr)
        return 2

    health = _check_health(args.base, strict=args.strict)
    if health is None:
        return 2

    panel = _load_panel(args.panel)
    if not panel:
        print(f"[error] panel {args.panel} has no tasks", file=sys.stderr)
        return 2

    print(f"[verify_routing_panel] {len(panel)} tasks @ {args.base}")
    results = [
        _probe(entry, base=args.base, api_key=args.api_key) for entry in panel
    ]

    exit_code = _format_report(results, args.base)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "panel": str(args.panel),
                    "how_base": args.base,
                    "health": health,
                    "results": [
                        {
                            "task_id": r.task_id,
                            "status": r.status,
                            "strategy": r.strategy,
                            "injected": r.injected,
                            "pattern_id": r.pattern_id,
                            "similarity": r.similarity,
                            "matched_symptom": r.matched_symptom,
                            "safety_label": r.safety_label,
                            "expected_pattern_any": list(r.expected_pattern_any),
                            "expected_strategy_any": list(r.expected_strategy_any),
                            "expected_safety_label_any": list(
                                r.expected_safety_label_any
                            ),
                            "fail_reason": r.fail_reason,
                            "latency_ms": r.latency_ms,
                        }
                        for r in results
                    ],
                    "summary": {
                        "total": len(results),
                        "pass": sum(1 for r in results if r.status == "pass"),
                        "fail": sum(1 for r in results if r.status == "fail"),
                        "unreachable": sum(
                            1 for r in results if r.status == "unreachable"
                        ),
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[verify_routing_panel] wrote {args.out}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
