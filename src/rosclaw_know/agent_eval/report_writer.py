"""Write Phase 9 benchmark artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from rosclaw_know import config
from rosclaw_know.ab_harness import ALL_ARMS, TaskRunResult, render_markdown, to_jsonable


def write_report(
    label: str,
    results: list[TaskRunResult],
    codes: dict[tuple[str, str, int], str],
) -> Path:
    """Persist a Phase 9 run under ``data/benchmarks/phase9_real_agent/<label>/``.

    Artifacts:
      - ``results.jsonl`` — one TaskRunResult per line.
      - ``trials.jsonl`` — per-trial code snapshots.
      - ``summary.json`` — full ``ab_harness.to_jsonable`` payload.
      - ``summary.md`` — markdown summary.
    """
    out_dir = config.BENCHMARKS_DIR / "phase9_real_agent" / label
    out_dir.mkdir(parents=True, exist_ok=True)

    results_path = out_dir / "results.jsonl"
    with open(results_path, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    trials_path = out_dir / "trials.jsonl"
    with open(trials_path, "w", encoding="utf-8") as fh:
        for (task_id, arm, seed), code in sorted(codes.items()):
            fh.write(
                json.dumps(
                    {"task_id": task_id, "arm": arm, "seed": seed, "code": code},
                    ensure_ascii=False,
                )
                + "\n"
            )

    summary = to_jsonable(results, arms=list(ALL_ARMS))
    summary_path = out_dir / "summary.json"
    tmp_summary = summary_path.with_suffix(summary_path.suffix + ".tmp")
    tmp_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_summary.replace(summary_path)

    md = render_markdown(results, arms=list(ALL_ARMS))
    md_path = out_dir / "summary.md"
    tmp_md = md_path.with_suffix(md_path.suffix + ".tmp")
    tmp_md.write_text(md, encoding="utf-8")
    tmp_md.replace(md_path)

    return out_dir


__all__ = ["write_report"]
