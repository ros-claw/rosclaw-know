#!/usr/bin/env python3
"""Sprint 7: build a :class:`TaskPack` for an agent from the CLI.

Wraps :func:`rosclaw_know.task_pack_builder.build_task_pack` with an
asset-loader for the canonical YAMLs.  Doubles as the back-end the
MCP tool (``rosclaw_task_pack``) and the FastAPI endpoint
(``POST /know/v1/task-pack/build``) call into.

Usage::

    # Print Markdown + JSON to stdout
    python scripts/build_task_pack.py --task-name pid_tuning

    # Write the pack to data/assets/task_packs/<id>.json
    python scripts/build_task_pack.py --task-name flash_attention --apply

    # Customise the iteration budget and recommendation depth
    python scripts/build_task_pack.py \\
        --task-name crypto_aes128 \\
        --budget-iterations 40 \\
        --top-k-patterns 8
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import yaml

from rosclaw_know import config
from rosclaw_know.schemas import (
    FailureMode,
    PatternCardV2,
    TaskCard,
    TaskPackQuery,
)
from rosclaw_know.task_pack_builder import (
    TaskCardNotFoundError,
    build_task_pack,
    render_markdown,
)

logger = logging.getLogger("build_task_pack")


# ── loaders ──────────────────────────────────────────────────────────────


def load_task_cards(path: Path) -> list[TaskCard]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [TaskCard.model_validate(t) for t in raw.get("task_cards", [])]


def load_pattern_cards(path: Path) -> list[PatternCardV2]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [PatternCardV2.model_validate(p) for p in raw.get("pattern_cards", [])]


def load_failures(path: Path) -> list[FailureMode]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [FailureMode.model_validate(f) for f in raw.get("failures", [])]


def load_assets() -> tuple[list[TaskCard], list[PatternCardV2], list[FailureMode]]:
    """One-stop loader pointing at the canonical asset paths."""
    return (
        load_task_cards(config.ASSETS_DIR / "task_cards.yaml"),
        load_pattern_cards(config.ASSETS_DIR / "pattern_cards_v2.yaml"),
        load_failures(config.ASSETS_DIR / "failure_taxonomy.yaml"),
    )


# ── CLI ──────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Build a TaskPack for an agent's pre-flight prompt "
            "(plan §10.1 reference implementation)."
        ),
    )
    p.add_argument(
        "--task-name", required=True,
        help="Task identifier (e.g. pid_tuning, flash_attention, crypto_aes128).",
    )
    p.add_argument(
        "--benchmark", default=None,
        help="Benchmark family hint (frontier-eng / arena / ...).",
    )
    p.add_argument(
        "--budget-iterations", type=int, default=20,
        help="Iteration budget the agent has (1-1_000_000).  Default 20.",
    )
    p.add_argument(
        "--top-k-patterns", type=int, default=5,
        help="How many patterns to recommend.  Default 5.",
    )
    p.add_argument(
        "--max-tokens", type=int, default=1200,
        help="Hard ceiling on the rendered pack length (default 1200).",
    )
    p.add_argument(
        "--apply", action="store_true",
        help="Write pack JSON to data/assets/task_packs/<id>.json (default: stdout only).",
    )
    p.add_argument(
        "--quiet-markdown", action="store_true",
        help="Skip the Markdown render in stdout (JSON only).",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    tasks, patterns, failures = load_assets()
    print(
        f"Loaded {len(tasks)} TaskCards, {len(patterns)} PatternCardV2s, "
        f"{len(failures)} FailureModes.",
        file=sys.stderr,
    )

    query = TaskPackQuery(
        task_name=args.task_name,
        benchmark=args.benchmark,
        budget_iterations=args.budget_iterations,
        top_k_patterns=args.top_k_patterns,
        max_tokens=args.max_tokens,
    )

    t0 = time.perf_counter()
    try:
        pack = build_task_pack(
            query, catalog=tasks, patterns=patterns, failures=failures,
        )
    except TaskCardNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    elapsed_ms = (time.perf_counter() - t0) * 1000

    if not args.quiet_markdown:
        print(render_markdown(pack))
    print(
        json.dumps(pack.model_dump(mode="json"), ensure_ascii=False, indent=2),
    )
    print(
        f"\nBuild latency: {elapsed_ms:.1f} ms  (plan §13 p95 target: <1500 ms)",
        file=sys.stderr,
    )
    print(
        f"Pack token estimate: {pack.token_estimate} "
        f"(query max_tokens: {query.max_tokens})",
        file=sys.stderr,
    )

    if args.apply:
        out_dir = config.ASSETS_DIR / "task_packs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{pack.task_pack_id}.json"
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(pack.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(out_path)
        print(f"OK  wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
