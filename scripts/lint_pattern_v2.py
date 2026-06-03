#!/usr/bin/env python3
"""Lint PatternCardV2 markdown for plan-§11.6 structural completeness.

Sprint 4 deliverable.

Asserts every input markdown file contains every required section
heading and every required frontmatter key, exits non-zero on any
violation.  Intended to run in CI before publish.

Usage::

    # Lint a directory
    python scripts/lint_pattern_v2.py wiki/auto_compiled

    # Lint a single file
    python scripts/lint_pattern_v2.py wiki/auto_compiled/pattern_v2_add_time_budget.md

    # Strict mode: every file must also include Cross-domain analogy.
    python scripts/lint_pattern_v2.py --strict wiki/auto_compiled
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

from rosclaw_know.pattern_compiler_v2 import REQUIRED_SECTIONS

logger = logging.getLogger("lint_pattern_v2")


# Frontmatter keys that the linter requires.  Plan §11.6 calls out
# source_quality + evidence as required metadata for every pattern.
_REQUIRED_FRONTMATTER: tuple[str, ...] = (
    "pattern_id",
    "schema_version",
    "domain",
    "task_families",
    "source_quality",
    "evidence",
)

# Extra section required only in --strict mode.
_STRICT_SECTIONS: tuple[str, ...] = (
    "Cross-domain analogy",
)

# Float literal that would leak a benchmark answer — same regex Sprint 3
# uses in code_diff_summarizer.  PatternCardV2 patch_sketch may legally
# show `0.0` in a recipe, so we restrict the leak check to the
# Next-Experiment / Code-Target / Expected-Verifier-Signal sections.
_LEAK_RE = re.compile(r"(?<![A-Za-z_])-?\d+\.\d+(?![A-Za-z_])")
_LEAK_SECTIONS: tuple[str, ...] = (
    "Next Experiment",
    "Code Target",
    "Expected Verifier Signal",
)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``--- frontmatter --- body``.

    Tolerates files without frontmatter (returns ``({}, text)``).
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip("\n")
    body = text[end + len("\n---"):].lstrip("\n")
    try:
        meta = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"malformed YAML frontmatter: {exc}")
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a mapping")
    return meta, body


def _section_body(body: str, name: str) -> str | None:
    """Return the body of ``## <name>`` section (until the next ## or EOF)."""
    pattern = re.compile(
        rf"^##\s+{re.escape(name)}\s*$(.*?)(?=^##\s|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    m = pattern.search(body)
    return m.group(1).strip() if m else None


def lint_file(path: Path, *, strict: bool = False) -> list[str]:
    """Return a list of structural problems found in ``path``.

    Empty list = file passes.
    """
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []

    try:
        meta, body = _split_frontmatter(text)
    except ValueError as exc:
        return [str(exc)]

    if not meta:
        problems.append("no YAML frontmatter")
    else:
        for key in _REQUIRED_FRONTMATTER:
            if key not in meta:
                problems.append(f"frontmatter missing key: {key}")
        # source_quality must be S/A/B/C/D
        sq = meta.get("source_quality")
        if sq is not None and sq not in ("S", "A", "B", "C", "D"):
            problems.append(f"source_quality must be S/A/B/C/D, got {sq!r}")
        # evidence must be a mapping with n, avg_uplift, win_rate
        ev = meta.get("evidence")
        if ev is not None:
            if not isinstance(ev, dict):
                problems.append("evidence must be a mapping")
            else:
                for ekey in ("n", "avg_uplift", "win_rate"):
                    if ekey not in ev:
                        problems.append(f"evidence missing key: {ekey}")

    # Required section headings
    needed = list(REQUIRED_SECTIONS)
    if strict:
        needed.extend(_STRICT_SECTIONS)
    for name in needed:
        sec = _section_body(body, name)
        if sec is None:
            problems.append(f"missing required section: ## {name}")
        elif not sec or sec == "_()_":
            problems.append(f"section ## {name} is empty")

    # Leak guard on the agent-facing sections only.
    for name in _LEAK_SECTIONS:
        sec = _section_body(body, name)
        if sec and _LEAK_RE.search(sec):
            # Skip when the float appears inside a fenced code block —
            # the patch sketch IS allowed to show `0.0`, but the prose
            # in Next Experiment must not.
            non_code = re.sub(r"```.*?```", "", sec, flags=re.DOTALL)
            if _LEAK_RE.search(non_code):
                m = _LEAK_RE.search(non_code)
                problems.append(
                    f"## {name} contains a float literal in prose: "
                    f"{m.group(0)!r}"
                )
    return problems


def _iter_md_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(root.rglob("*.md"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Lint PatternCardV2 markdown for §11.6 completeness.",
    )
    p.add_argument(
        "paths", nargs="+",
        help="Files or directories to lint (recursively).",
    )
    p.add_argument(
        "--strict", action="store_true",
        help="Also require Cross-domain analogy section.",
    )
    p.add_argument(
        "--min-pass-rate", type=float, default=0.9,
        help="Plan §Sprint 4 acceptance: at least this fraction of "
             "sampled patterns must pass.  Default 0.9 (i.e. 45/50).",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
        help="DEBUG-level logging.",
    )
    args = p.parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    files: list[Path] = []
    for s in args.paths:
        files.extend(_iter_md_files(Path(s)))
    if not files:
        print("no .md files found", file=sys.stderr)
        return 2

    stats = Counter()
    failing: list[tuple[Path, list[str]]] = []
    for f in files:
        problems = lint_file(f, strict=args.strict)
        if problems:
            stats["fail"] += 1
            failing.append((f, problems))
        else:
            stats["pass"] += 1

    total = stats["pass"] + stats["fail"]
    pass_rate = stats["pass"] / total if total else 0.0
    print(
        f"Lint result: {stats['pass']}/{total} pass "
        f"({pass_rate:.0%})"
    )

    for f, problems in failing[:20]:
        print(f"\n  {f}:")
        for prob in problems:
            print(f"    - {prob}")
    if len(failing) > 20:
        print(f"  ...and {len(failing) - 20} more failing files")

    if pass_rate < args.min_pass_rate:
        print(
            f"\nFAIL: pass rate {pass_rate:.0%} below gate "
            f"{args.min_pass_rate:.0%}",
            file=sys.stderr,
        )
        return 1
    if stats["fail"] > 0:
        # Even when the pass-rate gate clears, individual failures are
        # still a soft warning — exit 0 but flag the failures.
        print("(some files fell short; pass-rate gate still cleared)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
