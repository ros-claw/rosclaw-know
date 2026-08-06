#!/usr/bin/env python3
"""Verify HOW's live routing against the canonical routing panel.

Doc §8 Sprint 4 hard gate. POSTs each task's symptom query (verbatim mirror
of verify_frontier_eng.py BENCHMARK_SUITE) to HOW's /wiki/v1/prompt/build
and asserts the returned ``pattern_id`` / ``strategy`` / ``routing_trace``
matches the panel's contract.

This is Gate-A "Retrieval Correctness":
LLM-judge PANEL Δ is no longer the sole ship criterion. Routing must be
correct BEFORE any paired_ab is allowed to start.

Usage::

    python scripts/verify_routing_panel.py \
        --base http://127.0.0.1:8088 \
        --panel data/panels/routing_panel.yaml \
        --out data/reports/routing_iter5_p0.json \
        --markdown-out data/reports/routing_iter5_p0.md

Exit codes:
  0  all tasks pass
  1  one or more tasks FAIL — paired_ab must NOT launch
  2  HOW unreachable / panel malformed
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
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


@dataclass(frozen=True)
class PanelEntry:
    task_id: str
    task_type: str  # positive | collateral | adversarial
    query: str
    expected_pattern_any: tuple[str, ...]
    expected_strategy_any: tuple[str, ...]
    expected_safety_label_any: tuple[str, ...]
    expected_snippet_mode: str | None
    must_not_top1: tuple[str, ...]
    allow_abstain: bool
    collateral_protect: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class ProbeResult:
    task_id: str
    task_type: str
    status: str  # "pass" | "fail" | "unreachable"
    strategy: str | None
    injected: bool | None
    pattern_id: str | None
    similarity: float | None
    matched_symptom: str | None
    safety_label: str | None
    snippet_mode: str | None
    expected_pattern_any: tuple[str, ...]
    expected_strategy_any: tuple[str, ...]
    expected_safety_label_any: tuple[str, ...]
    must_not_top1: tuple[str, ...]
    allow_abstain: bool
    fail_reason: str | None
    latency_ms: float | None
    routing_trace: dict[str, Any] | None


def _load_panel(panel_path: Path) -> list[PanelEntry]:
    data = yaml.safe_load(panel_path.read_text(encoding="utf-8"))
    tasks_raw = data.get("tasks") or []
    out: list[PanelEntry] = []
    for entry in tasks_raw:
        out.append(
            PanelEntry(
                task_id=str(entry["task_id"]),
                task_type=str(entry.get("type") or "positive").lower(),
                query=str(entry["query"]).strip(),
                expected_pattern_any=tuple(entry.get("expected_pattern_any") or ()),
                expected_strategy_any=tuple(entry.get("expected_strategy_any") or ()),
                expected_safety_label_any=tuple(
                    entry.get("expected_safety_label_any") or ()
                ),
                expected_snippet_mode=entry.get("expected_snippet_mode"),
                must_not_top1=tuple(entry.get("must_not_top1") or ()),
                allow_abstain=bool(entry.get("allow_abstain", False)),
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
            "task_id": entry.task_id,
            "task_name": entry.task_id,
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
            task_type=entry.task_type,
            status="unreachable",
            strategy=None,
            injected=None,
            pattern_id=None,
            similarity=None,
            matched_symptom=None,
            safety_label=None,
            snippet_mode=None,
            expected_pattern_any=entry.expected_pattern_any,
            expected_strategy_any=entry.expected_strategy_any,
            expected_safety_label_any=entry.expected_safety_label_any,
            must_not_top1=entry.must_not_top1,
            allow_abstain=entry.allow_abstain,
            fail_reason=f"HTTP {exc.code}: {exc.reason}",
            latency_ms=None,
            routing_trace=None,
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            task_id=entry.task_id,
            task_type=entry.task_type,
            status="unreachable",
            strategy=None,
            injected=None,
            pattern_id=None,
            similarity=None,
            matched_symptom=None,
            safety_label=None,
            snippet_mode=None,
            expected_pattern_any=entry.expected_pattern_any,
            expected_strategy_any=entry.expected_strategy_any,
            expected_safety_label_any=entry.expected_safety_label_any,
            must_not_top1=entry.must_not_top1,
            allow_abstain=entry.allow_abstain,
            fail_reason=f"HOW unreachable: {exc}",
            latency_ms=None,
            routing_trace=None,
        )
    latency_ms = (time.perf_counter() - start) * 1000

    try:
        data = json.loads(raw)
    except ValueError:
        return ProbeResult(
            task_id=entry.task_id,
            task_type=entry.task_type,
            status="unreachable",
            strategy=None,
            injected=None,
            pattern_id=None,
            similarity=None,
            matched_symptom=None,
            safety_label=None,
            snippet_mode=None,
            expected_pattern_any=entry.expected_pattern_any,
            expected_strategy_any=entry.expected_strategy_any,
            expected_safety_label_any=entry.expected_safety_label_any,
            must_not_top1=entry.must_not_top1,
            allow_abstain=entry.allow_abstain,
            fail_reason=f"non-JSON response: {raw[:200]}",
            latency_ms=latency_ms,
            routing_trace=None,
        )

    strategy = data.get("strategy")
    pattern_id = data.get("pattern_id")
    similarity = data.get("similarity")
    matched_symptom = data.get("matched_symptom")
    injected = data.get("injected")
    snippet_mode = data.get("snippet_mode")
    routing_trace = data.get("routing_trace")
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

    # ── hard guard: must_not_top1 ────────────────────────────────────────
    if entry.must_not_top1 and pattern_id in entry.must_not_top1:
        reason = (
            f"forbidden top1 pattern_id={pattern_id!r} (injected={injected}, "
            f"strategy={strategy!r}); must_not_top1={list(entry.must_not_top1)}"
        )
        return ProbeResult(
            task_id=entry.task_id,
            task_type=entry.task_type,
            status="fail",
            strategy=strategy,
            injected=injected,
            pattern_id=pattern_id,
            similarity=similarity,
            matched_symptom=matched_symptom,
            safety_label=safety_label,
            snippet_mode=snippet_mode,
            expected_pattern_any=entry.expected_pattern_any,
            expected_strategy_any=entry.expected_strategy_any,
            expected_safety_label_any=entry.expected_safety_label_any,
            must_not_top1=entry.must_not_top1,
            allow_abstain=entry.allow_abstain,
            fail_reason=reason,
            latency_ms=latency_ms,
            routing_trace=routing_trace,
        )

    # ── snippet_mode check (informational, not a hard fail on its own) ───
    snippet_mode_ok = True
    if entry.expected_snippet_mode and snippet_mode is not None:
        snippet_mode_ok = snippet_mode == entry.expected_snippet_mode

    # ── pass rules ───────────────────────────────────────────────────────
    pass_via_pattern = (
        bool(entry.expected_pattern_any)
        and pattern_id is not None
        and pattern_id in entry.expected_pattern_any
    )
    pass_via_strategy = bool(entry.expected_strategy_any) and (
        (strategy is not None and strategy in entry.expected_strategy_any)
        or (
            effective_strategy is not None
            and effective_strategy in entry.expected_strategy_any
        )
    )
    pass_via_safety_label = (
        bool(entry.expected_safety_label_any)
        and strategy == "SAFETY"
        and injected is True
        and safety_label is not None
        and safety_label in entry.expected_safety_label_any
    )
    pass_via_abstain = (
        entry.allow_abstain
        and effective_strategy in ("ABSTAIN", "no_inject", "FREE_EXPLORATION")
    )

    if (
        pass_via_pattern
        or pass_via_strategy
        or pass_via_safety_label
        or pass_via_abstain
    ):
        status = "pass" if snippet_mode_ok else "fail"
        fail_reason = None
        if not snippet_mode_ok:
            fail_reason = (
                f"snippet_mode={snippet_mode!r}, expected "
                f"{entry.expected_snippet_mode!r}"
            )
        return ProbeResult(
            task_id=entry.task_id,
            task_type=entry.task_type,
            status=status,
            strategy=strategy,
            injected=injected,
            pattern_id=pattern_id,
            similarity=similarity,
            matched_symptom=matched_symptom,
            safety_label=safety_label,
            snippet_mode=snippet_mode,
            expected_pattern_any=entry.expected_pattern_any,
            expected_strategy_any=entry.expected_strategy_any,
            expected_safety_label_any=entry.expected_safety_label_any,
            must_not_top1=entry.must_not_top1,
            allow_abstain=entry.allow_abstain,
            fail_reason=fail_reason,
            latency_ms=latency_ms,
            routing_trace=routing_trace,
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
        task_type=entry.task_type,
        status="fail",
        strategy=strategy,
        injected=injected,
        pattern_id=pattern_id,
        similarity=similarity,
        matched_symptom=matched_symptom,
        safety_label=safety_label,
        snippet_mode=snippet_mode,
        expected_pattern_any=entry.expected_pattern_any,
        expected_strategy_any=entry.expected_strategy_any,
        expected_safety_label_any=entry.expected_safety_label_any,
        must_not_top1=entry.must_not_top1,
        allow_abstain=entry.allow_abstain,
        fail_reason=reason,
        latency_ms=latency_ms,
        routing_trace=routing_trace,
    )


def _check_health(base: str, *, strict: bool, timeout: float = 5.0) -> dict | None:
    """Hit /healthz and (when --strict) refuse if degraded."""
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


def _compute_metrics(results: list[ProbeResult]) -> dict[str, Any]:
    """Compute panel-level metrics for the report."""
    total = len(results)
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    unreachable = sum(1 for r in results if r.status == "unreachable")

    positives = [r for r in results if r.task_type == "positive"]
    adversarials = [r for r in results if r.task_type == "adversarial"]
    collateral_hits = [r for r in results if r.must_not_top1]

    positive_correct = sum(1 for r in positives if r.status == "pass")
    adversarial_pass = sum(1 for r in adversarials if r.status == "pass")
    collateral_pass = sum(1 for r in collateral_hits if r.status == "pass")

    false_injections = 0
    for r in results:
        if r.status != "pass" and r.pattern_id and r.injected:
            false_injections += 1
        if r.status != "pass" and r.task_type == "adversarial" and r.injected:
            false_injections += 1

    accuracy = positive_correct / len(positives) if positives else 0.0
    adversarial_fpr = (
        1.0 - (adversarial_pass / len(adversarials)) if adversarials else 0.0
    )
    collateral_fir = (
        1.0 - (collateral_pass / len(collateral_hits)) if collateral_hits else 0.0
    )
    false_injection_rate = false_injections / total if total else 0.0

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "unreachable": unreachable,
        "accuracy": round(accuracy, 4),
        "adversarial_false_positive_rate": round(adversarial_fpr, 4),
        "collateral_false_injection_rate": round(collateral_fir, 4),
        "false_injection_rate": round(false_injection_rate, 4),
        "positive_total": len(positives),
        "positive_pass": positive_correct,
        "adversarial_total": len(adversarials),
        "adversarial_pass": adversarial_pass,
        "collateral_guard_total": len(collateral_hits),
        "collateral_guard_pass": collateral_pass,
    }


def _format_report(results: list[ProbeResult], how_base: str, metrics: dict[str, Any]) -> int:
    """Stdout report + exit code."""
    print()
    print(
        f"{'task_id':<36} {'type':<12} {'status':<8} {'strategy':<14} "
        f"{'pattern_id':<40} {'sim':>6}"
    )
    print("─" * 113)
    for r in results:
        sim_str = f"{r.similarity:.4f}" if isinstance(r.similarity, (int, float)) else "  —  "
        pattern = (r.pattern_id or "—")[:40]
        strategy = (r.strategy or "—")[:14]
        print(
            f"{r.task_id:<36} {r.task_type:<12} {r.status:<8} {strategy:<14} "
            f"{pattern:<40} {sim_str:>6}"
        )
    print("─" * 113)
    print(f"@ HOW base = {how_base}")
    print(
        f"PASS={metrics['passed']}   FAIL={metrics['failed']}   "
        f"UNREACHABLE={metrics['unreachable']}   total={metrics['total']}"
    )
    print(
        f"accuracy={metrics['accuracy']:.2%}   "
        f"adversarial_fpr={metrics['adversarial_false_positive_rate']:.2%}   "
        f"collateral_fir={metrics['collateral_false_injection_rate']:.2%}   "
        f"false_injection_rate={metrics['false_injection_rate']:.2%}"
    )
    if metrics["failed"] or metrics["unreachable"]:
        print()
        print("Failures:")
        for r in results:
            if r.status != "pass":
                print(f"  {r.task_id:<36} → {r.fail_reason}")
        return 1 if metrics["unreachable"] == 0 else 2
    print("ALL PASS — routing panel cleared, paired_ab may launch.")
    return 0


def _write_markdown(
    path: Path,
    results: list[ProbeResult],
    health: dict,
    metrics: dict[str, Any],
    how_base: str,
) -> None:
    """Write a human-readable Markdown report."""
    lines: list[str] = [
        "# Routing Panel Report",
        "",
        f"- **HOW base**: `{how_base}`",
        f"- **router_backend**: `{health.get('router_backend', 'unknown')}`",
        f"- **healthz status**: `{health.get('status', 'unknown')}`",
        f"- **panel**: `{metrics['total']}` tasks",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Accuracy (positive) | {metrics['accuracy']:.2%} |",
        f"| Adversarial false-positive rate | {metrics['adversarial_false_positive_rate']:.2%} |",
        f"| Collateral false-injection rate | {metrics['collateral_false_injection_rate']:.2%} |",
        f"| Overall false-injection rate | {metrics['false_injection_rate']:.2%} |",
        f"| Passed | {metrics['passed']} / {metrics['total']} |",
        "",
        "## Results",
        "",
        "| task_id | type | status | strategy | pattern_id | similarity |",
        "|---------|------|--------|----------|------------|------------|",
    ]
    for r in results:
        sim_str = f"{r.similarity:.4f}" if isinstance(r.similarity, (int, float)) else "—"
        lines.append(
            f"| {r.task_id} | {r.task_type} | {r.status} | "
            f"{r.strategy or '—'} | {r.pattern_id or '—'} | {sim_str} |"
        )

    failures = [r for r in results if r.status != "pass"]
    if failures:
        lines.extend(["", "## Failures", ""])
        for r in failures:
            lines.append(f"- **{r.task_id}**: {r.fail_reason}")
    else:
        lines.extend(["", "## Decision", "", "ALL PASS — routing panel cleared."])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


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
        "--markdown-out",
        type=Path,
        default=None,
        help="Optional Markdown report path.",
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
    metrics = _compute_metrics(results)
    exit_code = _format_report(results, args.base, metrics)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "panel": str(args.panel),
                    "panel_schema_version": 2,
                    "how_base": args.base,
                    "health": health,
                    "metrics": metrics,
                    "results": [
                        {
                            "task_id": r.task_id,
                            "type": r.task_type,
                            "status": r.status,
                            "strategy": r.strategy,
                            "injected": r.injected,
                            "pattern_id": r.pattern_id,
                            "similarity": r.similarity,
                            "matched_symptom": r.matched_symptom,
                            "safety_label": r.safety_label,
                            "snippet_mode": r.snippet_mode,
                            "expected_pattern_any": list(r.expected_pattern_any),
                            "expected_strategy_any": list(r.expected_strategy_any),
                            "expected_safety_label_any": list(
                                r.expected_safety_label_any
                            ),
                            "must_not_top1": list(r.must_not_top1),
                            "allow_abstain": r.allow_abstain,
                            "fail_reason": r.fail_reason,
                            "latency_ms": r.latency_ms,
                            "routing_trace": r.routing_trace,
                        }
                        for r in results
                    ],
                    "summary": {
                        "total": metrics["total"],
                        "pass": metrics["passed"],
                        "fail": metrics["failed"],
                        "unreachable": metrics["unreachable"],
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[verify_routing_panel] wrote {args.out}")

    if args.markdown_out:
        _write_markdown(args.markdown_out, results, health, metrics, args.base)
        print(f"[verify_routing_panel] wrote {args.markdown_out}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
