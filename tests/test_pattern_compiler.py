"""Tests for the Sprint 4 PatternCardV2 compiler + linter."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from rosclaw_know.pattern_compiler_v2 import (
    REQUIRED_SECTIONS,
    CompileContext,
    compile_pattern_card,
    render_markdown,
)
from rosclaw_know.schemas import (
    CandidatePattern,
    FailureMode,
    Mutation,
    PatternCardV2,
)

# Pull the linter from scripts/.
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts")
)
import lint_pattern_v2 as linter  # noqa: E402

# ── fixtures ──────────────────────────────────────────────────────────


def _make_candidate(
    *,
    fam: str = "robotics_optimization",
    failure_id: str | None = "failure_pid_integrator_windup",
    mut_kinds: list[str] | None = None,
    evidence_count: int = 5,
) -> CandidatePattern:
    """Build a CandidatePattern that exercises every compiler branch."""
    mut_kinds = mut_kinds or ["set_parameter_zero"]
    mutations = [
        Mutation(
            kind=k,
            description="set parameter to zero on Ki_z" if k == "set_parameter_zero"
                        else "added some intervention",
            target_identifier="Ki_z" if k == "set_parameter_zero" else None,
        )
        for k in mut_kinds
    ]
    return CandidatePattern(
        id="candidate_test_pattern",
        task_family=fam,
        failure_id=failure_id,
        diagnosis="Setting integral gains to zero stabilises the controller.",
        successful_mutations=mutations,
        expected_verifier_signal="overshoot decreases; settling time goes down",
        evidence_count=evidence_count,
        avg_score_delta=0.05,
        source_trajectory_ids=["t1", "t2"],
    )


def _make_failure(fid: str = "failure_pid_integrator_windup") -> FailureMode:
    return FailureMode(
        id=fid,
        name="PID Windup",
        domain="Control_Locomotion",
        symptom_text="Actuator saturates while integral keeps accumulating.",
        normalized_symptom="saturation_with_integral",
        observable_signals=["output clipped for many steps"],
        likely_causes=["unconditional integration"],
        contraindications=["do not raise Ki"],
        severity="safety_critical",
    )


# ── compile_pattern_card unit tests ────────────────────────────────────


def test_compile_basic_round_trip() -> None:
    cand = _make_candidate()
    card = compile_pattern_card(cand)
    assert isinstance(card, PatternCardV2)
    assert card.id == "compiled_test_pattern"
    assert card.task_families == ["robotics_optimization"]
    assert card.domain == "Control_Locomotion"


def test_compile_pulls_symptom_from_failure_mode() -> None:
    """If a matching FailureMode is in context, its symptom_text wins."""
    fm = _make_failure()
    cand = _make_candidate(failure_id=fm.id)
    card = compile_pattern_card(cand, context=CompileContext(failure_modes={fm.id: fm}))
    assert "actuator saturates" in card.symptom.lower()
    assert "unconditional integration" in card.diagnosis.lower()


def test_compile_falls_back_when_no_failure_mode() -> None:
    """No FailureMode → fall back to candidate.diagnosis-derived symptom."""
    cand = _make_candidate(failure_id=None)
    card = compile_pattern_card(cand)
    assert card.symptom
    assert "integral gains" in card.diagnosis.lower()


def test_compile_assigns_correct_source_quality() -> None:
    """evidence_count ≥ 5 → A, else B."""
    a = compile_pattern_card(_make_candidate(evidence_count=10))
    b = compile_pattern_card(_make_candidate(evidence_count=2))
    assert a.source_quality == "A"
    assert b.source_quality == "B"


def test_compile_next_experiment_is_action_template() -> None:
    cand = _make_candidate(mut_kinds=["set_parameter_zero", "add_time_budget"])
    card = compile_pattern_card(cand)
    # Imperatives for both kinds present
    assert "Set the named parameter(s) to zero" in card.next_experiment
    assert "wall-clock deadline" in card.next_experiment


def test_compile_priority_defaults_to_staging() -> None:
    """Sprint-4 compiled patterns land in staging (priority=0).
    Promotion needs Sprint-6 evidence loop."""
    card = compile_pattern_card(_make_candidate())
    assert card.priority == 0


def test_compile_unknown_family_resolves_to_default() -> None:
    cand = _make_candidate(fam="totally_unknown_family")
    card = compile_pattern_card(cand)
    assert card.domain == "Systems_Compute"  # default
    assert card.task_families == ["totally_unknown_family"]


# ── render_markdown unit tests ────────────────────────────────────────


def test_render_includes_every_required_section() -> None:
    cand = _make_candidate(mut_kinds=["set_parameter_zero"])
    card = compile_pattern_card(cand)
    md = render_markdown(card)
    for name in REQUIRED_SECTIONS:
        assert f"## {name}" in md, f"missing ## {name}"


def test_render_yaml_frontmatter_valid() -> None:
    """The emitted frontmatter must parse as YAML and contain required keys."""
    import yaml
    cand = _make_candidate()
    card = compile_pattern_card(cand)
    md = render_markdown(card)
    assert md.startswith("---\n")
    end = md.find("\n---\n", 4)
    assert end != -1
    meta = yaml.safe_load(md[4:end])
    assert isinstance(meta, dict)
    for k in ("pattern_id", "schema_version", "domain", "source_quality", "evidence"):
        assert k in meta


def test_render_truncates_long_source_ids() -> None:
    """source_ids longer than 8 entries get a 'truncated' marker."""
    cand = _make_candidate()
    cand = cand.model_copy(update={
        "source_trajectory_ids": [f"t{i}" for i in range(20)],
    })
    card = compile_pattern_card(cand)
    md = render_markdown(card)
    assert "truncated" in md


def test_render_no_float_leaks_in_prose_sections() -> None:
    """## Next Experiment / Code Target / Expected Verifier Signal must
    not contain bare float literals in prose."""
    import re
    cand = _make_candidate(mut_kinds=["set_parameter_zero"])
    card = compile_pattern_card(cand)
    md = render_markdown(card)
    # Pull each prose section
    for section_name in ("Next Experiment", "Code Target",
                          "Expected Verifier Signal"):
        m = re.search(
            rf"^##\s+{re.escape(section_name)}\s*$(.*?)(?=^##|\Z)",
            md, re.DOTALL | re.MULTILINE,
        )
        assert m is not None
        prose = re.sub(r"```.*?```", "", m.group(1), flags=re.DOTALL)
        floats = re.findall(r"(?<![A-Za-z_])-?\d+\.\d+(?![A-Za-z_])", prose)
        assert not floats, f"leak in {section_name}: {floats}"


# ── linter unit tests ─────────────────────────────────────────────────


def test_linter_accepts_valid_card(tmp_path: Path) -> None:
    cand = _make_candidate()
    card = compile_pattern_card(cand)
    md_file = tmp_path / "valid.md"
    md_file.write_text(render_markdown(card))
    problems = linter.lint_file(md_file)
    assert problems == []


def test_linter_flags_missing_section(tmp_path: Path) -> None:
    bad = """---
pattern_id: bad
schema_version: "2.0"
domain: Systems_Compute
task_families: [test]
source_quality: C
evidence:
  n: 0
  avg_uplift: 0.0
  win_rate: 0.0
---
# Title
## Symptom

content
## Diagnosis

content
## Preconditions

content
## Next Experiment

content
## Code Target

content
"""  # missing Expected Verifier Signal, Anti-pattern, Contraindications
    md_file = tmp_path / "bad.md"
    md_file.write_text(bad)
    problems = linter.lint_file(md_file)
    assert any("Expected Verifier Signal" in p for p in problems)
    assert any("Anti-pattern" in p for p in problems)
    assert any("Contraindications" in p for p in problems)


def test_linter_flags_bad_source_quality(tmp_path: Path) -> None:
    bad = '''---
pattern_id: bad
schema_version: "2.0"
domain: Systems_Compute
task_families: [test]
source_quality: Z
evidence: {n: 1, avg_uplift: 0.0, win_rate: 0.0}
---
'''
    f = tmp_path / "bad.md"
    f.write_text(bad)
    problems = linter.lint_file(f)
    assert any("source_quality" in p for p in problems)


def test_linter_flags_float_leak_in_prose(tmp_path: Path) -> None:
    bad = """---
pattern_id: bad
schema_version: "2.0"
domain: Systems_Compute
task_families: [test]
source_quality: A
evidence:
  n: 5
  avg_uplift: 0.0
  win_rate: 0.0
---
# x
## Symptom
foo
## Diagnosis
foo
## Preconditions
- p
## Next Experiment
Set Ki_z = 0.0142 in your code.
## Code Target
target
## Expected Verifier Signal
- s
## Anti-pattern
- a
## Contraindications
- c
"""
    f = tmp_path / "bad.md"
    f.write_text(bad)
    problems = linter.lint_file(f)
    assert any("float literal" in p.lower() for p in problems)


def test_linter_tolerates_floats_in_fenced_code_blocks(tmp_path: Path) -> None:
    good = """---
pattern_id: good
schema_version: "2.0"
domain: Systems_Compute
task_families: [test]
source_quality: A
evidence:
  n: 5
  avg_uplift: 0.0
  win_rate: 0.0
---
# Title
## Symptom
foo
## Diagnosis
foo
## Preconditions
- p
## Next Experiment
- set the param to zero

```python
x = 0.0142   # inside a code block; allowed
```

## Code Target
target
## Expected Verifier Signal
- s
## Anti-pattern
- a
## Contraindications
- c
"""
    f = tmp_path / "good.md"
    f.write_text(good)
    problems = linter.lint_file(f)
    assert problems == [], f"unexpected: {problems}"


# ── integration: compile all 8 Sprint-3 candidates ────────────────────


_REPO = Path(__file__).resolve().parents[1]
SPRINT3_CATALOG = _REPO / "data/assets/trajectory_patterns.yaml"
TAXONOMY = _REPO / "data/assets/failure_taxonomy.yaml"
HAS_S3 = SPRINT3_CATALOG.is_file() and TAXONOMY.is_file()


@pytest.mark.skipif(not HAS_S3, reason="Sprint-3 catalog not generated yet")
def test_compile_all_sprint3_candidates_pass_lint(tmp_path: Path) -> None:
    """End-to-end: every Sprint-3 candidate compiles to a lint-clean
    PatternCardV2 markdown."""
    import yaml
    cat = yaml.safe_load(SPRINT3_CATALOG.read_text(encoding="utf-8"))
    tax = yaml.safe_load(TAXONOMY.read_text(encoding="utf-8"))
    fms = {f["id"]: FailureMode.model_validate(f) for f in tax["failures"]}
    ctx = CompileContext(failure_modes=fms)

    for raw in cat["candidate_patterns"]:
        cand = CandidatePattern.model_validate(raw)
        card = compile_pattern_card(cand, context=ctx)
        md = render_markdown(card)
        # Write to tmp + lint
        f = tmp_path / f"pattern_{card.id}.md"
        f.write_text(md)
        problems = linter.lint_file(f)
        assert problems == [], f"{cand.id}: {problems}"


@pytest.mark.skipif(not HAS_S3, reason="Sprint-3 catalog not generated yet")
def test_compile_failure_mode_overrides_symptom() -> None:
    """When the candidate matches a curated FailureMode, the compiled
    card's symptom comes from that FailureMode — not the candidate's
    heuristic phrasing."""
    import yaml
    cat = yaml.safe_load(SPRINT3_CATALOG.read_text(encoding="utf-8"))
    tax = yaml.safe_load(TAXONOMY.read_text(encoding="utf-8"))
    fms = {f["id"]: FailureMode.model_validate(f) for f in tax["failures"]}
    ctx = CompileContext(failure_modes=fms)

    # Find the Sprint-3 anti-windup candidate
    target_id = "candidate_zero_integral_gain_on_saturation"
    raw = next(c for c in cat["candidate_patterns"] if c["id"] == target_id)
    cand = CandidatePattern.model_validate(raw)
    card = compile_pattern_card(cand, context=ctx)
    # FailureMode symptom_text mentions "actuator saturates"
    assert "actuator saturates" in card.symptom.lower()
