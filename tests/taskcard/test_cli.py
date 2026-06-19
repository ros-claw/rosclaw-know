"""CLI tests for TaskCard v1."""
from __future__ import annotations

from pathlib import Path

import yaml

from rosclaw_know.taskcard.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
SCENE = FIXTURES / "scenes" / "lab_soccer.yaml"
GOLD = FIXTURES / "gold" / "g1_kick_ball.gold.yaml"


def test_cli_compile_writes_files(tmp_path: Path):
    out_dir = tmp_path / "out"
    rc = main(
        [
            "compile",
            "--task",
            "g1_kick_ball",
            "--robot",
            "unitree_g1",
            "--scene",
            str(SCENE),
            "--output-dir",
            str(out_dir),
            "--strict",
        ]
    )
    assert rc == 0
    assert (out_dir / "g1_kick_ball.taskcard.yaml").exists()
    assert (out_dir / "g1_kick_ball.evidence.jsonl").exists()
    assert (out_dir / "g1_kick_ball.compile_report.md").exists()


def test_cli_validate_passes(tmp_path: Path):
    out_dir = tmp_path / "out"
    main(
        [
            "compile",
            "--task",
            "g1_kick_ball",
            "--robot",
            "unitree_g1",
            "--scene",
            str(SCENE),
            "--output-dir",
            str(out_dir),
        ]
    )
    card_path = out_dir / "g1_kick_ball.taskcard.yaml"
    rc = main(["validate", "--taskcard", str(card_path)])
    assert rc == 0


def test_cli_eval_taskcard_passes(tmp_path: Path):
    out_dir = tmp_path / "out"
    main(
        [
            "compile",
            "--task",
            "g1_kick_ball",
            "--robot",
            "unitree_g1",
            "--scene",
            str(SCENE),
            "--output-dir",
            str(out_dir),
        ]
    )
    card_path = out_dir / "g1_kick_ball.taskcard.yaml"
    rc = main(["eval-taskcard", "--taskcard", str(card_path), "--gold", str(GOLD)])
    assert rc == 0


def test_cli_export_hooks(tmp_path: Path):
    out_dir = tmp_path / "out"
    main(
        [
            "compile",
            "--task",
            "g1_kick_ball",
            "--robot",
            "unitree_g1",
            "--scene",
            str(SCENE),
            "--output-dir",
            str(out_dir),
        ]
    )
    card_path = out_dir / "g1_kick_ball.taskcard.yaml"
    hooks_dir = tmp_path / "hooks"
    rc = main(["export-hooks", "--taskcard", str(card_path), "--out", str(hooks_dir)])
    assert rc == 0
    assert (hooks_dir / "memory_queries.yaml").exists()
    assert (hooks_dir / "how_hooks.yaml").exists()
    assert (hooks_dir / "auto_hooks.yaml").exists()

    memory = yaml.safe_load((hooks_dir / "memory_queries.yaml").read_text(encoding="utf-8"))
    assert memory["queries"]


def test_cli_compile_invalid_task_fails():
    rc = main(["compile", "--task", "ur5_kick_ball", "--robot", "ur5"])
    assert rc == 1
