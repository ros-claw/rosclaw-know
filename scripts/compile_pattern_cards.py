#!/usr/bin/env python3
"""Compile Sprint-3 :class:`CandidatePattern`s into Sprint-4 PatternCardV2 markdown.

Sprint 4 deliverable (plan §11.6).

Reads ``data/assets/trajectory_patterns.yaml`` (output of Sprint 3)
and ``data/assets/failure_taxonomy.yaml`` (Sprint 1) and emits one
action-template markdown per candidate to
``wiki/auto_compiled/pattern_v2_<id>.md``.

Usage::

    # Dry-run summary
    python scripts/compile_pattern_cards.py

    # Write the markdowns
    python scripts/compile_pattern_cards.py --apply
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

import yaml

from rosclaw_know import config
from rosclaw_know.pattern_compiler_v2 import (
    REQUIRED_SECTIONS,
    CompileContext,
    compile_pattern_card,
    render_markdown,
)
from rosclaw_know.schemas import CandidatePattern, FailureMode

logger = logging.getLogger("compile_pattern_cards")


_DEFAULT_OUT_DIR = config.ASSETS_DIR / "compiled_patterns"


def _load_failure_modes(path: Path) -> dict[str, FailureMode]:
    """Parse ``failure_taxonomy.yaml`` into a ``failure_id → FailureMode`` lookup.

    Returns an empty dict if the file is missing; missing taxonomy is
    a soft warning, not a hard failure.
    """
    if not path.is_file():
        logger.warning("failure taxonomy not found at %s", path)
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, FailureMode] = {}
    for f in raw.get("failures", []):
        try:
            fm = FailureMode.model_validate(f)
            out[fm.id] = fm
        except Exception as exc:
            logger.warning("skipping malformed failure %r: %s", f.get("id"), exc)
    return out


def _load_candidates(path: Path) -> list[CandidatePattern]:
    """Parse the Sprint-3 trajectory_patterns.yaml."""
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found — run Sprint 3 extractor first")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [CandidatePattern.model_validate(c) for c in raw.get("candidate_patterns", [])]


def _section_present(text: str, name: str) -> bool:
    """Strict heading match (``## <name>``) to dodge subsection collisions."""
    return f"## {name}" in text


# ── CLI ──────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compile CandidatePattern → PatternCardV2 markdown.",
    )
    p.add_argument(
        "--candidates",
        default=str(config.ASSETS_DIR / "trajectory_patterns.yaml"),
        help="Source candidate-pattern YAML "
             "(default: data/assets/trajectory_patterns.yaml).",
    )
    p.add_argument(
        "--failure-taxonomy",
        default=str(config.ASSETS_DIR / "failure_taxonomy.yaml"),
        help="failure_taxonomy.yaml (Sprint 1).",
    )
    p.add_argument(
        "--out-dir",
        default=str(_DEFAULT_OUT_DIR),
        help="Destination directory for the markdown files "
             "(default: data/assets/compiled_patterns/).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write the markdowns.  Without --apply this is dry-run.",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="DEBUG-level logging.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    candidates = _load_candidates(Path(args.candidates))
    failure_modes = _load_failure_modes(Path(args.failure_taxonomy))
    ctx = CompileContext(failure_modes=failure_modes)

    # Compile and render — keep both in memory so the linter can pre-flight.
    rendered: list[tuple[str, str]] = []  # (filename, body)
    summary = Counter()
    for cand in candidates:
        try:
            card = compile_pattern_card(cand, context=ctx)
            body = render_markdown(card)
        except Exception as exc:
            logger.error("failed to compile %s: %s", cand.id, exc)
            summary["compile_failed"] += 1
            continue

        # Linter — every required section must appear.
        missing = [s for s in REQUIRED_SECTIONS if not _section_present(body, s)]
        if missing:
            logger.error(
                "compiled %s missing sections: %s", card.id, missing
            )
            summary["lint_failed"] += 1
            continue

        filename = f"pattern_v2_{card.id.removeprefix('compiled_')}.md"
        rendered.append((filename, body))
        summary["ok"] += 1

    # Print summary.
    print(f"Compiled {summary['ok']}/{len(candidates)} patterns")
    for k, v in summary.items():
        if k != "ok":
            print(f"  {k}: {v}")

    # Acceptance gates §11.6:
    #   - all generated patterns must include Diagnosis / Preconditions /
    #     Next Experiment / Expected Verifier Signal / Contraindications
    #   - each must declare source_quality
    if summary.get("lint_failed", 0) > 0 or summary.get("compile_failed", 0) > 0:
        print("FAIL: at least one pattern failed compilation or linting.", file=sys.stderr)
        return 1

    if summary["ok"] == 0:
        print("FAIL: no patterns compiled — refusing to declare success.", file=sys.stderr)
        return 1

    if not args.apply:
        print(f"\nDRY-RUN — would write {summary['ok']} files to {args.out_dir}")
        print("(use --apply to write)")
        for fn, _ in rendered[:3]:
            print(f"  preview: {fn}")
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for fn, body in rendered:
        target = out_dir / fn
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(target)
    print(f"OK  wrote {len(rendered)} files to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
