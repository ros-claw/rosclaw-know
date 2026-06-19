"""CLI for ROSClaw-Know TaskCard v1 compiler."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from .compiler import TaskCardCompileError, TaskCardCompiler
from .schemas import TaskCard


def _compile(args: argparse.Namespace) -> int:
    compiler = TaskCardCompiler()
    try:
        card = compiler.compile(
            task_id=args.task,
            goal=args.goal,
            robot=args.robot,
            robot_id=args.robot_id,
            body_path=args.body,
            embodiment_path=args.embodiment,
            eurdf_path=args.eurdf,
            scene_path=args.scene,
            scene_id=args.scene_id,
            enable_memory=args.memory,
            enable_cognitive_wiki=args.cognitive_wiki,
            strict=args.strict,
        )
    except TaskCardCompileError as exc:
        print(f"[rosclaw-know] ❌ Compile failed: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(yaml.safe_dump(card.model_dump(mode="json"), sort_keys=False, allow_unicode=True))
        return 0

    output_dir = Path(args.output_dir)
    paths = compiler.compile_to_files(
        args.task,
        output_dir,
        goal=args.goal,
        robot=args.robot,
        robot_id=args.robot_id,
        body_path=args.body,
        embodiment_path=args.embodiment,
        eurdf_path=args.eurdf,
        scene_path=args.scene,
        scene_id=args.scene_id,
        enable_memory=args.memory,
        enable_cognitive_wiki=args.cognitive_wiki,
        strict=args.strict,
    )

    print("[rosclaw-know] ✅ TaskCard compiled")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    path = Path(args.taskcard)
    if not path.exists():
        print(f"[rosclaw-know] ❌ File not found: {path}", file=sys.stderr)
        return 1

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        TaskCard.model_validate(data)
    except Exception as exc:
        print(f"[rosclaw-know] ❌ Validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"[rosclaw-know] ✅ {path} is a valid TaskCard v1")
    return 0


def _eval_taskcard(args: argparse.Namespace) -> int:
    card_path = Path(args.taskcard)
    gold_path = Path(args.gold)
    for p in (card_path, gold_path):
        if not p.exists():
            print(f"[rosclaw-know] ❌ File not found: {p}", file=sys.stderr)
            return 1

    card_data = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    gold_data = yaml.safe_load(gold_path.read_text(encoding="utf-8"))

    card = TaskCard.model_validate(card_data)

    report: dict[str, Any] = {
        "task_id": card.metadata.task_id,
        "passed": True,
        "checks": [],
    }

    checks = report["checks"]

    expected_subtasks = gold_data.get("expected_subtasks", [])
    actual_ids = [st.id for st in card.subtasks]
    missing = [sid for sid in expected_subtasks if sid not in actual_ids]
    subtask_cov = (len(expected_subtasks) - len(missing)) / max(len(expected_subtasks), 1)
    checks.append({"name": "subtask_coverage", "score": subtask_cov, "missing": missing})

    expected_constraints = gold_data.get("expected_constraints", [])
    all_constraint_ids = _collect_constraint_ids(card.physical_constraints)
    missing_constraints = [cid for cid in expected_constraints if cid not in all_constraint_ids]
    constraint_cov = (len(expected_constraints) - len(missing_constraints)) / max(len(expected_constraints), 1)
    checks.append({"name": "constraint_coverage", "score": constraint_cov, "missing": missing_constraints})

    expected_failures = gold_data.get("expected_failures", [])
    actual_failures = {f["id"] for f in card.failure_taxonomy.get("failures", [])}
    missing_failures = [fid for fid in expected_failures if fid not in actual_failures]
    failure_cov = (len(expected_failures) - len(missing_failures)) / max(len(expected_failures), 1)
    checks.append({"name": "failure_coverage", "score": failure_cov, "missing": missing_failures})

    min_confidence = gold_data.get("min_compile_confidence", 0.0)
    checks.append({"name": "compile_confidence", "score": card.quality.compile_confidence, "min": min_confidence})

    if missing or missing_constraints or missing_failures or card.quality.compile_confidence < min_confidence:
        report["passed"] = False

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"[rosclaw-know] Evaluation for {card.metadata.task_id}")
        for check in checks:
            icon = "✅" if not check.get("missing") and check.get("score", 0) >= check.get("min", 0) else "❌"
            print(f"  {icon} {check['name']}: {check['score']:.2f}")
            if check.get("missing"):
                print(f"      missing: {', '.join(check['missing'])}")

    return 0 if report["passed"] else 1


def _collect_constraint_ids(physical_constraints: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("hard_constraints", "soft_constraints", "operational_constraints", "context_constraints"):
        for item in physical_constraints.get(key, []):
            if isinstance(item, dict) and "id" in item:
                ids.add(item["id"])
    return ids


def _export_hooks(args: argparse.Namespace) -> int:
    card_path = Path(args.taskcard)
    if not card_path.exists():
        print(f"[rosclaw-know] ❌ File not found: {card_path}", file=sys.stderr)
        return 1

    data = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    card = TaskCard.model_validate(data)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    memory_path = out_dir / "memory_queries.yaml"
    how_path = out_dir / "how_hooks.yaml"
    auto_path = out_dir / "auto_hooks.yaml"

    memory_path.write_text(
        yaml.safe_dump(card.memory_hooks.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    how_path.write_text(
        yaml.safe_dump(card.how_hooks.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    auto_path.write_text(
        yaml.safe_dump(card.auto_hooks.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    print(f"[rosclaw-know] ✅ Hooks exported to {out_dir}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rosclaw-know-taskcard",
        description="ROSClaw-Know TaskCard v1 compiler CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    compile_parser = subparsers.add_parser("compile", help="Compile a task into a TaskCard")
    compile_parser.add_argument("--task", required=True, help="Task ID")
    compile_parser.add_argument("--goal", default=None, help="Natural language goal")
    compile_parser.add_argument("--robot", default="unitree_g1", help="Robot model")
    compile_parser.add_argument("--robot-id", default=None, help="Robot instance ID")
    compile_parser.add_argument("--body", default=None, help="body.yaml path")
    compile_parser.add_argument("--embodiment", default=None, help="EMBODIMENT.md path")
    compile_parser.add_argument("--eurdf", default=None, help="e-URDF path")
    compile_parser.add_argument("--scene", default=None, help="Scene file path")
    compile_parser.add_argument("--scene-id", default=None, help="Scene ID")
    compile_parser.add_argument("--memory", action="store_true", default=True, help="Enable memory hooks")
    compile_parser.add_argument("--no-memory", action="store_false", dest="memory", help="Disable memory hooks")
    compile_parser.add_argument("--cognitive-wiki", action="store_true", default=True, help="Enable cognitive wiki sync")
    compile_parser.add_argument("--no-cognitive-wiki", action="store_false", dest="cognitive_wiki", help="Disable cognitive wiki sync")
    compile_parser.add_argument("--strict", action="store_true", help="Enable strict validation")
    compile_parser.add_argument("--dry-run", action="store_true", help="Print TaskCard to stdout without writing files")
    compile_parser.add_argument("--output-dir", default=".rosclaw/know/taskcards", help="Output directory")

    validate_parser = subparsers.add_parser("validate", help="Validate a TaskCard YAML file")
    validate_parser.add_argument("--taskcard", required=True, help="Path to TaskCard YAML")

    eval_parser = subparsers.add_parser("eval-taskcard", help="Evaluate a TaskCard against a gold fixture")
    eval_parser.add_argument("--taskcard", required=True, help="Path to generated TaskCard YAML")
    eval_parser.add_argument("--gold", required=True, help="Path to gold YAML")
    eval_parser.add_argument("--json", action="store_true", help="Output JSON report")

    export_parser = subparsers.add_parser("export-hooks", help="Export memory/how/auto hooks from a TaskCard")
    export_parser.add_argument("--taskcard", required=True, help="Path to TaskCard YAML")
    export_parser.add_argument("--out", required=True, help="Output directory")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "compile":
        return _compile(args)
    if args.command == "validate":
        return _validate(args)
    if args.command == "eval-taskcard":
        return _eval_taskcard(args)
    if args.command == "export-hooks":
        return _export_hooks(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
