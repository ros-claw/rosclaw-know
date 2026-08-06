"""Tests for the Frontier-Eng / benchmark TaskCard extractor (Sprint 2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from rosclaw_know.extractors import (
    extract_from_corpus,
    extract_task_card,
    load_task_dir,
)
from rosclaw_know.extractors.benchmark_extractor import (
    FAMILY_RECOMMENDATIONS,
    FAMILY_TO_DOMAIN,
    _camel_to_snake,
    _detect_artifact_type,
    _detect_metric_name,
    _detect_objective_direction,
    _detect_verifier_type,
    _extract_baseline_description,
    _extract_hard_constraints,
    is_parent_index,
)
from rosclaw_know.schemas import FRONTIER_DOMAINS, TaskCard

# Real Frontier-Eng corpus.  Tests that read it are gated on its presence
# so the suite still passes in a fresh checkout.
FRONTIER_ROOT = Path(__file__).resolve().parents[2] / "Frontier-Engineering" / "benchmarks"
HAS_CORPUS = FRONTIER_ROOT.is_dir()


# ── small unit tests for the heuristics ─────────────────────────────────


def test_camel_to_snake() -> None:
    assert _camel_to_snake("CarAerodynamicsSensing") == "car_aerodynamics_sensing"
    assert _camel_to_snake("PIDTuning") == "pid_tuning"
    assert _camel_to_snake("AES-128") == "aes_128"
    assert _camel_to_snake("MLA") == "mla"


def test_detect_artifact_from_initial_program() -> None:
    assert _detect_artifact_type("baseline/AES-128.cpp", "") == "cpp"
    assert _detect_artifact_type("scripts/init.py", "") == "python"
    assert _detect_artifact_type("baseline/foo.cu", "") == "cuda"
    assert _detect_artifact_type("solver.yaml", "") == "yaml"


def test_detect_artifact_from_text_fallback() -> None:
    """When no initial_program, fall back to text scan."""
    assert _detect_artifact_type(None, "Use a Triton kernel") == "triton"
    assert _detect_artifact_type(None, "CUDA kernel optimized") == "cuda"
    assert _detect_artifact_type(None, "Implement in C++") == "cpp"
    assert _detect_artifact_type(None, "no hints here") == "python"


# ── direction is metric-anchored where possible ─────────────────────────


def test_direction_anchored_on_known_metric() -> None:
    # Even if the text says "minimize execution time", a combined_score
    # metric is always higher-better.
    assert _detect_objective_direction(
        "Minimize execution time", metric_name="combined_score"
    ) == "maximize"


def test_direction_makespan_is_minimize() -> None:
    assert _detect_objective_direction(
        "Higher is better.", metric_name="makespan"
    ) == "minimize"


def test_direction_throughput_is_maximize() -> None:
    assert _detect_objective_direction("", metric_name="throughput") == "maximize"


def test_direction_falls_back_to_prose_for_unknown_metric() -> None:
    """combined_score is the heuristic default — only falls through when
    no metric was detected (i.e. an artificially constructed case)."""
    # Unknown metric → prose scan kicks in.
    txt = "Goal: minimize the tracking error across all scenarios."
    assert _detect_objective_direction(txt, metric_name="custom_metric") == "minimize"
    txt = "Achieve higher is better on score."
    assert _detect_objective_direction(txt, metric_name="custom_metric") == "maximize"


# ── metric detection ────────────────────────────────────────────────────


def test_detect_metric_first_match_wins() -> None:
    assert _detect_metric_name("Higher combined_score is better.") == "combined_score"
    assert _detect_metric_name("Minimize makespan.") == "makespan"
    assert _detect_metric_name("Compute ITAE-style.") == "itae"
    assert _detect_metric_name("nothing matches here") == "combined_score"


# ── verifier classification ────────────────────────────────────────────


def test_verifier_kernel_harness() -> None:
    assert _detect_verifier_type(
        eval_command="python eval.py benchmark",
        initial_program="baseline/submission.py",
        task_md_text="GPU kernel optimization with Triton.",
    ) == "benchmark_harness"


def test_verifier_simulator() -> None:
    assert _detect_verifier_type(
        eval_command="python evaluator.py",
        initial_program=None,
        task_md_text="Rollout the simulator under wind disturbance.",
    ) == "simulator"


def test_verifier_checker_script_default() -> None:
    assert _detect_verifier_type(
        eval_command="python evaluator.py",
        initial_program=None,
        task_md_text="Compute final score via verifier.",
    ) == "checker_script"


# ── hard constraints extraction ────────────────────────────────────────


def test_constraints_numbered_form() -> None:
    txt = "1) Only modify baseline.cpp.\n2) Preserve interfaces.\n3) Validate first."
    out = _extract_hard_constraints(constraints_text=txt, task_md_text="")
    assert len(out) == 3
    assert "Only modify baseline.cpp." in out


def test_constraints_bullet_form() -> None:
    txt = "- bound output\n- validate input\n- no NaN"
    out = _extract_hard_constraints(constraints_text=txt, task_md_text="")
    assert out == ["bound output", "validate input", "no NaN"]


def test_constraints_bare_prose_fallback() -> None:
    """JobShop-style: lines without numbering still count as constraints."""
    txt = "Optimize baseline/init.py for this JobShop family.\nObjective: minimize makespan.\nKeep solution as pure Python."
    out = _extract_hard_constraints(constraints_text=txt, task_md_text="")
    assert len(out) == 3


def test_constraints_dedup() -> None:
    txt = "1) keep API stable\n2) keep API stable\n3) other"
    out = _extract_hard_constraints(constraints_text=txt, task_md_text="")
    assert out == ["keep API stable", "other"]


def test_constraints_from_task_md_feasibility_rules() -> None:
    md = (
        "## 6. Feasibility Rules\n\n"
        "A submission is infeasible if:\n\n"
        "1. a required key is missing\n"
        "2. a gain is not numeric\n"
        "3. a gain is outside its configured range\n\n"
        "## 7. Objective\n"
    )
    out = _extract_hard_constraints(constraints_text=None, task_md_text=md)
    assert "a required key is missing" in out
    assert "a gain is not numeric" in out
    assert "a gain is outside its configured range" in out


# ── baseline description ───────────────────────────────────────────────


def test_baseline_description_from_problem_section() -> None:
    md = "# Title\n\n## 1. Problem\n\nTune 12 gains of a PID controller.\nMore details follow."
    out = _extract_baseline_description(md)
    assert out.startswith("Tune 12 gains")


def test_baseline_description_empty_input() -> None:
    assert _extract_baseline_description("") == ""


# ── parent-index detection ─────────────────────────────────────────────


def test_is_parent_index_with_child_task_md(tmp_path: Path) -> None:
    parent = tmp_path / "FamilyParent"
    parent.mkdir()
    (parent / "Task.md").write_text("Index file pointing at children.")
    sub = parent / "ChildTask"
    sub.mkdir()
    (sub / "Task.md").write_text("# Real task body" * 50)
    assert is_parent_index(parent) is True
    assert is_parent_index(sub) is False


def test_is_parent_index_with_llm_prompt_child(tmp_path: Path) -> None:
    parent = tmp_path / "EngDesign"
    parent.mkdir()
    (parent / "Task.md").write_text("All tasks are from EngDesign repo.")  # < 400 chars
    sub = parent / "AM_02"
    sub.mkdir()
    (sub / "LLM_prompt.txt").write_text("real task")
    assert is_parent_index(parent) is True


def test_is_parent_index_stub_task_md(tmp_path: Path) -> None:
    """Very-short Task.md is treated as a stub even without children."""
    d = tmp_path / "Stubby"
    d.mkdir()
    (d / "Task.md").write_text("Stub.")  # < 400 chars
    assert is_parent_index(d) is True


# ── full extract_task_card happy path ──────────────────────────────────


def test_extract_task_card_pid_like(tmp_path: Path) -> None:
    task_dir = tmp_path / "Robotics" / "PIDTuning"
    task_dir.mkdir(parents=True)
    (task_dir / "Task.md").write_text(
        "# PID Tuning\n\n"
        "## 1. Problem\n\n"
        "Tune PID gains for a 2D quadrotor.\n\n"
        "## 6. Feasibility Rules\n\n"
        "1. a required key is missing\n"
        "2. a gain is outside its configured range\n\n"
        "## 7. Objective\n\n"
        "Minimize tracking error over time across all scenarios.\n"
        "It computes ITAE-style quantities."
    )
    fe = task_dir / "frontier_eval"
    fe.mkdir()
    (fe / "initial_program.txt").write_text("scripts/init.py")
    (fe / "eval_command.txt").write_text("python evaluator.py")
    (fe / "constraints.txt").write_text(
        "UnifiedTask constraints:\n"
        "1) Only modify scripts/init.py.\n"
        "2) Preserve function signatures."
    )

    inp = load_task_dir(task_dir)
    assert inp is not None
    card = extract_task_card(inp)
    assert isinstance(card, TaskCard)
    assert card.id == "task_robotics_pid_tuning"
    assert card.benchmark == "frontier-eng"
    assert card.task_name == "PIDTuning"
    assert card.task_family == "robotics_optimization"
    assert card.domain == "Control_Locomotion"
    assert card.artifact_type == "python"
    assert card.metric_name == "itae"
    assert card.objective_direction == "minimize"
    # "scenarios" in the synthesized text triggers simulator-class
    # verifier — matches what the real PIDTuning Task.md classifies as.
    assert card.verifier_type == "simulator"
    assert len(card.hard_constraints) >= 2
    assert "failure_pid_integrator_windup" in card.common_failure_modes
    assert "anti_windup_pid" in card.recommended_patterns


def test_extract_task_card_aes_like(tmp_path: Path) -> None:
    task_dir = tmp_path / "Cryptographic" / "AES-128"
    task_dir.mkdir(parents=True)
    (task_dir / "Task.md").write_text(
        "# AES-128 CTR\n\n"
        "Improve C++ AES-128 algorithm implementation efficiency (throughput)."
    )
    fe = task_dir / "frontier_eval"
    fe.mkdir()
    (fe / "initial_program.txt").write_text("baseline/AES-128.cpp")
    (fe / "constraints.txt").write_text(
        "1) Only modify `baseline/AES-128.cpp`.\n"
        "2) Preserve interfaces."
    )

    inp = load_task_dir(task_dir)
    assert inp is not None
    card = extract_task_card(inp)
    assert card.artifact_type == "cpp"
    assert card.metric_name == "throughput"
    assert card.objective_direction == "maximize"
    assert card.domain == "Systems_Compute"


def test_load_task_dir_missing(tmp_path: Path) -> None:
    """A dir without Task.md returns None."""
    d = tmp_path / "Nope"
    d.mkdir()
    assert load_task_dir(d) is None


def test_extract_task_card_rejects_unknown_domain(tmp_path: Path) -> None:
    """Synthesise an ExtractInput with a family not in FAMILY_TO_DOMAIN.

    Default to Systems_Compute — never crash on unknown family.
    """
    task_dir = tmp_path / "Unknown_Family" / "WidgetTask"
    task_dir.mkdir(parents=True)
    (task_dir / "Task.md").write_text("Build a widget.")
    inp = load_task_dir(task_dir)
    assert inp is not None
    card = extract_task_card(inp)
    assert card.domain == "Systems_Compute"
    assert card.task_family == "unknown_family_optimization"


# ── consistency invariants for the recommendation maps ────────────────


def test_family_recommendations_symmetric_with_domain_map() -> None:
    """Every family in FAMILY_TO_DOMAIN has an entry in
    FAMILY_RECOMMENDATIONS (and vice versa).

    This is the invariant that prevents a card from silently coming back
    with empty common_failure_modes because someone added a family to
    one map but forgot the other.
    """
    assert set(FAMILY_TO_DOMAIN.keys()) == set(FAMILY_RECOMMENDATIONS.keys())


def test_all_recommended_failure_ids_have_failure_prefix() -> None:
    for fam, rec in FAMILY_RECOMMENDATIONS.items():
        for fid in rec.get("common_failure_modes", []):
            assert fid.startswith("failure_"), f"{fam}: {fid}"


def test_all_recommended_pattern_ids_are_curated() -> None:
    """Recommended patterns must exist in data/assets/code_patterns/
    as curated entries (no `pattern_` prefix)."""
    from rosclaw_know import config
    patterns_dir = config.CODE_PATTERNS_DIR
    if not patterns_dir.is_dir():
        pytest.skip("code_patterns/ not present in this checkout")
    curated_ids = {
        p.stem for p in patterns_dir.glob("*.md")
        if not p.name.startswith("pattern_")
    }
    for fam, rec in FAMILY_RECOMMENDATIONS.items():
        for pid in rec.get("recommended_patterns", []):
            assert pid in curated_ids, (
                f"{fam} recommends {pid!r} which is not a curated "
                f"pattern.  Available: {sorted(curated_ids)}"
            )


def test_all_mapped_domains_are_frontier_domains() -> None:
    for fam, dom in FAMILY_TO_DOMAIN.items():
        assert dom in FRONTIER_DOMAINS, f"{fam} → {dom!r}"


# ── integration: real Frontier-Eng corpus ──────────────────────────────


@pytest.mark.skipif(not HAS_CORPUS, reason="Frontier-Eng corpus not available")
def test_corpus_produces_at_least_47_cards() -> None:
    cards = extract_from_corpus(FRONTIER_ROOT)
    # Plan §11.3 acceptance: ≥ 47 cards.
    assert len(cards) >= 47, (
        f"Frontier-Eng v1 expects ≥ 47 cards, got {len(cards)}"
    )


@pytest.mark.skipif(not HAS_CORPUS, reason="Frontier-Eng corpus not available")
def test_corpus_every_card_has_three_required_fields() -> None:
    """Plan §11.3:
    - every card MUST include objective_direction
    - every card MUST include artifact_type
    - every card MUST include verifier_type
    """
    cards = extract_from_corpus(FRONTIER_ROOT)
    for c in cards:
        assert c.objective_direction in ("maximize", "minimize"), c.id
        assert c.artifact_type, c.id
        assert c.verifier_type, c.id


@pytest.mark.skipif(not HAS_CORPUS, reason="Frontier-Eng corpus not available")
def test_corpus_every_card_validates_via_pydantic() -> None:
    cards = extract_from_corpus(FRONTIER_ROOT)
    for c in cards:
        # Round-trip — must pass strict validation.
        TaskCard.model_validate(c.model_dump())


@pytest.mark.skipif(not HAS_CORPUS, reason="Frontier-Eng corpus not available")
def test_corpus_unique_ids() -> None:
    """Ids must be unique — if two tasks collide we'd silently overwrite
    one of them downstream."""
    cards = extract_from_corpus(FRONTIER_ROOT)
    ids = [c.id for c in cards]
    assert len(ids) == len(set(ids)), (
        f"Duplicate ids: {[i for i in ids if ids.count(i) > 1]}"
    )


@pytest.mark.skipif(not HAS_CORPUS, reason="Frontier-Eng corpus not available")
def test_corpus_known_task_extracts_correctly() -> None:
    """PIDTuning is a known anchor — verify its derived fields."""
    cards = extract_from_corpus(FRONTIER_ROOT)
    by_name = {c.task_name: c for c in cards}
    assert "PIDTuning" in by_name
    pid = by_name["PIDTuning"]
    assert pid.task_family == "robotics_optimization"
    assert pid.domain == "Control_Locomotion"
    assert pid.artifact_type == "python"
    assert pid.metric_name == "itae"
    assert pid.objective_direction == "minimize"
    assert "anti_windup_pid" in pid.recommended_patterns
