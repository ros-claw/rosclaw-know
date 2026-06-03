#!/usr/bin/env python3
"""v1 → v1.5 / v2 bridge_index migration.

The v1.5 plan §4.2 / §11.9 calls for a **non-destructive** upgrade of
``data/assets/bridge_index.json``: every existing v1 field stays at its
original location so ``rosclaw-how`` can keep reading it verbatim, but
every cluster gains a new ``metadata`` block with the typed v2 info
(lifecycle status, source quality, evidence aggregate, embodiment
hints, …).

Properties
----------

* **Idempotent.** Re-running the script on an already-migrated bridge
  produces the same diff.  ``--check`` re-runs in dry-run mode and
  exits 1 if any cluster would change.
* **Non-destructive.** Existing top-level fields
  (``standard_name``, ``domain``, ``associated_patterns``,
  ``cross_domain_analogies``, ``priority``, ``is_staging``, ``source``,
  ``uplift_mean``, ``uplift_n``, ``win_rate``, ``last_seen``,
  ``safety_label``) are NOT modified.  Only ``metadata`` is added.
* **Lifecycle aware.** Each cluster's ``metadata.lifecycle_status`` is
  derived from its ``priority`` field:

    ===========  ============================
    priority     lifecycle_status
    ===========  ============================
    1            production
    0            staging
    -1           demoted
    None         needs_validation  (legacy)
    ===========  ============================

* **Schema-validated output.** Writes only after the migrated tree
  passes :func:`rosclaw_know.schemas.validate_bridge`, so a successful
  run guarantees v2 conformance.

Usage::

    python scripts/migrate_assets_v1_to_v2.py             # in-place, prompt-protected
    python scripts/migrate_assets_v1_to_v2.py --apply     # overwrite (after dry-run)
    python scripts/migrate_assets_v1_to_v2.py --check     # CI gate: exit 1 if drift
    python scripts/migrate_assets_v1_to_v2.py --out FILE  # write to a different path
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from rosclaw_know import config
from rosclaw_know.schemas import (
    SCHEMA_VERSION,
    LifecycleStatus,
    SourceQualityLevel,
    validate_bridge,
)

logger = logging.getLogger("migrate_assets_v1_to_v2")


# Map priority → lifecycle_status used by §3.1 of the plan.
_PRIORITY_TO_LIFECYCLE: dict[int | None, LifecycleStatus] = {
    1: "production",
    0: "staging",
    -1: "demoted",
    None: "needs_validation",
}


def _infer_source_quality(cluster: dict[str, Any]) -> SourceQualityLevel:
    """Best-effort guess of source quality from v1 fields.

    Rules:
      * `source == "curated"` → S (hand-written, fully verified)
      * `source.startswith("awesome:")` → C (curated list source)
      * `source == "autodraft"` → D (LLM draft, untrusted)
      * `source == "muse"` → B (LLM mined from a real source page)
      * otherwise → C (default; lets the v2 evidence loop upgrade later)
    """
    src = cluster.get("source")
    if src == "curated":
        return "S"
    if isinstance(src, str) and src.startswith("awesome:"):
        return "C"
    if src == "autodraft":
        return "D"
    if src == "muse":
        return "B"
    return "C"


def _evidence_block(cluster: dict[str, Any]) -> dict[str, Any]:
    """Build the metadata.evidence block from v1 phase-4 fields."""
    return {
        "n": int(cluster.get("uplift_n") or 0),
        "avg_uplift": float(cluster.get("uplift_mean") or 0.0),
        "win_rate": float(cluster.get("win_rate") or 0.0),
        "hint_use_rate": 0.0,
        "last_seen": str(cluster.get("last_seen") or ""),
        "placebo_adjusted_uplift": None,
    }


def _migrate_cluster(cluster: dict[str, Any]) -> dict[str, Any]:
    """Inject `metadata` into a single cluster (idempotent).

    If `metadata` already exists, we re-derive `lifecycle_status` and
    `evidence` from current v1 fields (which may have been re-weighted
    since the last migration) but preserve any task_families /
    embodiment_types / artifact_languages / contraindications the
    operator may have hand-edited.
    """
    out = deepcopy(cluster)
    existing = dict(out.get("metadata") or {})

    new_metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "lifecycle_status": _PRIORITY_TO_LIFECYCLE.get(
            out.get("priority"), "needs_validation"
        ),
        "task_families":       list(existing.get("task_families") or []),
        "embodiment_types":    list(existing.get("embodiment_types") or []),
        "artifact_languages":  list(existing.get("artifact_languages") or []),
        "objective_directions": list(existing.get("objective_directions") or []),
        "verifier_signals":    list(existing.get("verifier_signals") or []),
        "preconditions":       list(existing.get("preconditions") or []),
        "contraindications":   list(existing.get("contraindications") or []),
        "source_quality": existing.get("source_quality") or _infer_source_quality(out),
        "source_ids":          list(existing.get("source_ids") or []),
        "evidence": _evidence_block(out),
    }
    out["metadata"] = new_metadata
    return out


def migrate(bridge: dict[str, Any]) -> dict[str, Any]:
    """Apply non-destructive v2 migration to a parsed bridge_index dict."""
    out = deepcopy(bridge)
    clusters = out.get("symptom_clusters") or {}
    if not isinstance(clusters, dict):
        raise TypeError(
            f"symptom_clusters must be a dict, got {type(clusters).__name__}"
        )
    out["symptom_clusters"] = {cid: _migrate_cluster(c) for cid, c in clusters.items()}

    # Normalize safety_label_index value shapes (str → [str]).
    sli = out.get("safety_label_index") or {}
    out["safety_label_index"] = {
        k: ([v] if isinstance(v, str) else list(v)) for k, v in sli.items()
    }

    # Stamp the document's schema_version so downstream code can detect
    # already-migrated trees.
    out["schema_version"] = SCHEMA_VERSION
    return out


# ── CLI ────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Migrate bridge_index.json from v1 to v2.")
    p.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Source bridge_index.json (default: data/assets/bridge_index.json).",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Destination path (default: in-place over `input`).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write the migrated tree.  Without --apply the script is dry-run.",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="CI gate: exit 1 if migration would change anything.  Implies dry-run.",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG-level logging.",
    )
    return p.parse_args(argv)


def _summarize_diff(before: dict, after: dict) -> dict[str, int]:
    """Counters useful for the user's eyes."""
    counts = {
        "clusters": 0,
        "added_metadata": 0,
        "updated_metadata": 0,
        "lifecycle_production": 0,
        "lifecycle_staging": 0,
        "lifecycle_demoted": 0,
        "lifecycle_needs_validation": 0,
    }
    before_clusters = before.get("symptom_clusters") or {}
    after_clusters = after["symptom_clusters"]
    for cid, c in after_clusters.items():
        counts["clusters"] += 1
        prior = before_clusters.get(cid) or {}
        if not prior.get("metadata"):
            counts["added_metadata"] += 1
        elif prior.get("metadata") != c.get("metadata"):
            counts["updated_metadata"] += 1
        ls = c["metadata"]["lifecycle_status"]
        counts[f"lifecycle_{ls}"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    in_path = Path(args.input) if args.input else (config.ASSETS_DIR / "bridge_index.json")
    out_path = Path(args.out) if args.out else in_path

    if not in_path.exists():
        print(f"ERROR: {in_path} not found", file=sys.stderr)
        return 2

    raw = json.loads(in_path.read_text(encoding="utf-8"))
    migrated = migrate(raw)

    # Validate before we write — refuses to corrupt the file.
    try:
        validate_bridge(migrated)
    except Exception as exc:  # pydantic.ValidationError
        print("ERROR: migrated tree fails v2 validation, refusing to write:",
              file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1

    diff = _summarize_diff(raw, migrated)
    drift = diff["added_metadata"] + diff["updated_metadata"] > 0

    if args.check:
        if drift:
            print(
                f"DRIFT: migration would touch {diff['added_metadata']} new + "
                f"{diff['updated_metadata']} updated cluster metadata entries.",
                file=sys.stderr,
            )
            for k, v in diff.items():
                print(f"  {k:>30}: {v}", file=sys.stderr)
            return 1
        print(f"OK  no drift, {diff['clusters']} clusters already at v2.")
        return 0

    if not args.apply:
        # Dry-run summary.
        print(f"DRY-RUN — would write {out_path}")
        for k, v in diff.items():
            print(f"  {k:>30}: {v}")
        print("(use --apply to actually write; --check for CI gate)")
        return 0

    # Apply: write atomically (tmp + rename) so an interrupted run
    # doesn't leave a half-written bridge_index.
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_path)
    print(f"OK  migrated → {out_path}")
    for k, v in diff.items():
        print(f"  {k:>30}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
