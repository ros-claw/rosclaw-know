"""Sprint 5: tests for the hybrid retriever (plan §6.3, §Sprint 5 accept).

Acceptance checks:

* PID query top-5 contains ≥3 PID/control-related patterns
* CUDA query top-5 contains ≥3 systems/perf patterns
* World_Physics query is not dominated by Planning_Decision
* Demoted (priority=-1) patterns are excluded from top-k by default
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rosclaw_know.hybrid_retriever import (
    RankerQuery,
    ScoreBreakdown,
    default_semantic_fn,
    rank_pattern,
    top_k,
)
from rosclaw_know.schemas import EvidenceBlock, PatternCardV2

# ── factory ──────────────────────────────────────────────────────────────


def _card(
    *,
    pid: str,
    domain: str = "Systems_Compute",
    task_families: list[str] | None = None,
    embodiments: list[str] | None = None,
    symptom: str = "something is wrong",
    diagnosis: str = "the diagnosis",
    next_experiment: str = "try this fix",
    verifier_signals: list[str] | None = None,
    contraindications: list[str] | None = None,
    priority: int | None = None,
    evidence_n: int = 0,
    evidence_win_rate: float = 0.0,
    code_target: str = "",
) -> PatternCardV2:
    return PatternCardV2(
        id=pid,
        domain=domain,
        task_families=task_families or [],
        embodiment_types=embodiments or [],
        artifact_languages=["python"],
        priority=priority,
        symptom=symptom,
        diagnosis=diagnosis,
        preconditions=[],
        next_experiment=next_experiment,
        code_target=code_target,
        patch_sketch="",
        expected_verifier_signals=verifier_signals or [],
        anti_patterns=[],
        contraindications=contraindications or [],
        cross_domain_analogy="",
        source_quality="A",
        source_ids=[],
        evidence=EvidenceBlock(n=evidence_n, win_rate=evidence_win_rate),
    )


# ── default_semantic_fn unit tests ───────────────────────────────────────


def test_jaccard_self_similarity_is_one() -> None:
    assert default_semantic_fn("hello world", "hello world") == 1.0


def test_jaccard_disjoint_is_zero() -> None:
    assert default_semantic_fn("alpha beta", "gamma delta") == 0.0


def test_jaccard_empty_inputs_safe() -> None:
    assert default_semantic_fn("", "") == 0.0
    assert default_semantic_fn("foo", "") == 0.0


# ── rank_pattern unit tests ──────────────────────────────────────────────


def test_rank_pattern_returns_breakdown() -> None:
    p = _card(pid="compiled_p1")
    q = RankerQuery(text="something is wrong", keywords=())
    sb = rank_pattern(q, p)
    assert isinstance(sb, ScoreBreakdown)
    assert sb.semantic > 0.0


def test_task_family_boost_fires() -> None:
    p = _card(pid="compiled_p1", task_families=["pid_tuning"])
    q = RankerQuery(text="something", task_family="pid_tuning")
    sb = rank_pattern(q, p)
    assert sb.task_family == 1.0


def test_task_family_no_match_zero() -> None:
    p = _card(pid="compiled_p1", task_families=["pid_tuning"])
    q = RankerQuery(text="something", task_family="cuda_kernel")
    sb = rank_pattern(q, p)
    assert sb.task_family == 0.0


def test_embodiment_match_fires() -> None:
    p = _card(pid="compiled_p1", embodiments=["uav"])
    q = RankerQuery(text="x", embodiment_type="uav")
    sb = rank_pattern(q, p)
    assert sb.embodiment == 1.0


def test_evidence_score_uses_log1p_weighting() -> None:
    """0 samples → 0; high-n high-win-rate → high but ≤ 1."""
    p_zero = _card(pid="compiled_zero", evidence_n=0)
    p_small = _card(pid="compiled_small", evidence_n=2, evidence_win_rate=1.0)
    p_big = _card(pid="compiled_big", evidence_n=20, evidence_win_rate=1.0)

    q = RankerQuery(text="x")
    z = rank_pattern(q, p_zero).evidence
    s = rank_pattern(q, p_small).evidence
    b = rank_pattern(q, p_big).evidence

    assert z == 0.0
    assert 0 < s < b <= 1.0


def test_contraindication_penalty_fires() -> None:
    """When the agent declares a contraindication and the pattern lists
    one with overlapping vocabulary, the score is penalised."""
    p_safe = _card(pid="compiled_safe", contraindications=[])
    p_risky = _card(
        pid="compiled_risky",
        contraindications=["raising the integral gain on a saturated actuator"],
    )

    q = RankerQuery(
        text="control problem",
        contraindications=("raising integral gain",),
    )
    safe = rank_pattern(q, p_safe).total
    risky = rank_pattern(q, p_risky).total
    assert risky < safe
    assert rank_pattern(q, p_risky).contraindication == 1.0


def test_external_semantic_fn_used() -> None:
    """Caller-supplied semantic_fn must be invoked."""
    called: list[tuple[str, str]] = []

    def fake_sem(q: str, p: str) -> float:
        called.append((q, p))
        return 0.42

    p = _card(pid="compiled_p1")
    q = RankerQuery(text="foo")
    sb = rank_pattern(q, p, semantic_fn=fake_sem)
    assert called, "semantic_fn was not called"
    assert sb.semantic == 0.42


def test_total_equals_weighted_sum() -> None:
    """Plan §6.3 formula must hold to 1e-9."""
    p = _card(
        pid="compiled_check",
        task_families=["pid_tuning"],
        embodiments=["uav"],
        verifier_signals=["settling_time_below_threshold"],
        evidence_n=10,
        evidence_win_rate=1.0,
    )
    q = RankerQuery(
        text="actuator saturates",
        task_family="pid_tuning",
        embodiment_type="uav",
        verifier_signals=("settling_time_below_threshold",),
    )
    sb = rank_pattern(q, p)
    expected = (
        0.35 * sb.semantic
        + 0.15 * sb.bm25
        + 0.15 * sb.task_family
        + 0.10 * sb.embodiment
        + 0.10 * sb.verifier_signal
        + 0.10 * sb.evidence
        - 0.20 * sb.contraindication
    )
    assert abs(sb.total - expected) < 1e-9


# ── top_k acceptance tests (Sprint 5) ────────────────────────────────────


@pytest.fixture
def mixed_catalog() -> list[PatternCardV2]:
    """Catalog covering PID / CUDA / planning / world-physics buckets."""
    return [
        # PID-related
        _card(
            pid="compiled_anti_windup",
            domain="Control_Locomotion",
            task_families=["pid_tuning", "robotics_optimization"],
            embodiments=["uav", "manipulator"],
            symptom="PID controller actuator saturates during integral windup",
            diagnosis="actuator saturates while integral keeps accumulating",
            next_experiment="zero the integral gain when output is saturated",
            verifier_signals=["settling_time_below_threshold", "overshoot below"],
            evidence_n=8, evidence_win_rate=0.75,
        ),
        _card(
            pid="compiled_output_clamp",
            domain="Control_Locomotion",
            task_families=["pid_tuning"],
            embodiments=["uav"],
            symptom="output clamp missing on PID controller",
            diagnosis="controller output unbounded saturates actuator",
            next_experiment="clamp the controller output before actuator",
            verifier_signals=["actuator command within limits"],
            evidence_n=14, evidence_win_rate=0.80,
        ),
        _card(
            pid="compiled_pid_feedforward",
            domain="Control_Locomotion",
            task_families=["pid_tuning", "robotics_optimization"],
            embodiments=["manipulator"],
            symptom="PID controller slow response to large setpoint changes",
            diagnosis="missing feedforward compensation",
            next_experiment="add feedforward term to PID controller",
            evidence_n=3, evidence_win_rate=0.6,
        ),
        # CUDA / GPU / Systems
        _card(
            pid="compiled_vectorize_loop",
            domain="Systems_Compute",
            task_families=["kernel_engineering_optimization"],
            embodiments=["gpu_kernel"],
            symptom="CUDA inner loop in python dominates runtime",
            diagnosis="python loop overhead is bottleneck for CUDA kernel call",
            next_experiment="vectorize CUDA inner loop with triton",
            verifier_signals=["throughput improves on GPU"],
            evidence_n=20, evidence_win_rate=0.9,
        ),
        _card(
            pid="compiled_warm_start",
            domain="Systems_Compute",
            task_families=["kernel_engineering_optimization"],
            embodiments=["gpu_kernel"],
            symptom="CUDA kernel cold start each time wastes compute",
            diagnosis="warm start CUDA from prior best avoids cold start",
            next_experiment="warm start CUDA kernel from cached prior best",
            evidence_n=10, evidence_win_rate=0.7,
        ),
        _card(
            pid="compiled_gpu_memory_tiling",
            domain="Systems_Compute",
            task_families=["kernel_engineering_optimization"],
            embodiments=["gpu_kernel"],
            symptom="CUDA kernel memory bandwidth bottleneck",
            diagnosis="GPU L2 cache thrashing in CUDA matrix loop",
            next_experiment="tile CUDA matrix operations for cache locality",
            verifier_signals=["bandwidth utilization above 80 percent"],
            evidence_n=15, evidence_win_rate=0.85,
        ),
        # Planning_Decision
        _card(
            pid="compiled_replan_trigger",
            domain="Planning_Decision",
            task_families=["motion_planning"],
            embodiments=["wheeled_robot"],
            symptom="planner does not replan when state diverges from plan",
            diagnosis="open-loop plan diverges from realised trajectory",
            next_experiment="add state-error trigger for replanning",
            evidence_n=4, evidence_win_rate=0.65,
        ),
        # World_Physics
        _card(
            pid="compiled_contact_stiffness",
            domain="World_Physics",
            task_families=["contact_simulation"],
            embodiments=["humanoid"],
            symptom="contact simulation unstable physics dynamics friction",
            diagnosis="contact stiffness too high for solver step world physics",
            next_experiment="reduce contact stiffness or use implicit solver physics",
            verifier_signals=["simulator stable physics contact friction"],
            evidence_n=6, evidence_win_rate=0.7,
        ),
        _card(
            pid="compiled_mesh_collision",
            domain="World_Physics",
            task_families=["contact_simulation"],
            embodiments=["humanoid"],
            symptom="mesh collision physics dynamics friction blowup",
            diagnosis="penetration depth grows physics dynamics friction simulation",
            next_experiment="enable continuous collision detection physics",
            evidence_n=5, evidence_win_rate=0.6,
        ),
        # A demoted pattern (priority=-1)
        _card(
            pid="compiled_demoted_pid",
            domain="Control_Locomotion",
            task_families=["pid_tuning"],
            symptom="aggressive Kp PID controller integral windup actuator",
            diagnosis="raise Kp to fix windup PID controller",
            next_experiment="raise Kp to overcome saturation PID controller",
            priority=-1,
            evidence_n=2, evidence_win_rate=0.1,
        ),
    ]


def test_pid_query_top5_has_at_least_3_relevant(mixed_catalog) -> None:
    """Plan §Sprint 5 acceptance: PID query top-5 ≥ 3 relevant."""
    q = RankerQuery(
        text="PID controller actuator saturates integral windup",
        task_family="pid_tuning",
        embodiment_type="uav",
    )
    hits = top_k(q, mixed_catalog, k=5)
    relevant = [h for h, _ in hits if h.domain == "Control_Locomotion"]
    assert len(relevant) >= 3, [(h.id, sb.total) for h, sb in hits]


def test_cuda_query_top5_has_at_least_3_relevant(mixed_catalog) -> None:
    """Plan §Sprint 5 acceptance: CUDA query top-5 ≥ 3 relevant."""
    q = RankerQuery(
        text="CUDA kernel python loop overhead vectorize memory bandwidth",
        task_family="kernel_engineering_optimization",
        embodiment_type="gpu_kernel",
    )
    hits = top_k(q, mixed_catalog, k=5)
    relevant = [h for h, _ in hits if h.domain == "Systems_Compute"]
    assert len(relevant) >= 3, [(h.id, sb.total) for h, sb in hits]


def test_world_physics_query_not_dominated_by_planning(mixed_catalog) -> None:
    """Plan §Sprint 5 acceptance: World_Physics query not dominated by Planning_Decision."""
    q = RankerQuery(
        text="physics simulation contact dynamics friction blow up mesh",
        domain_hint="World_Physics",
    )
    hits = top_k(q, mixed_catalog, k=3)
    domains = [h.domain for h, _ in hits]
    # Either the top result is a World_Physics pattern, or at least
    # there's more World_Physics than Planning_Decision in the top-3.
    wp = sum(1 for d in domains if d == "World_Physics")
    pl = sum(1 for d in domains if d == "Planning_Decision")
    assert wp >= pl, f"top-3 domains: {domains}"
    assert wp >= 1, f"no World_Physics pattern in top-3: {domains}"


def test_demoted_pattern_excluded_from_topk(mixed_catalog) -> None:
    """Plan §Sprint 5 acceptance: priority=-1 patterns excluded from top-k."""
    q = RankerQuery(
        text="PID controller integral windup actuator saturation",
        task_family="pid_tuning",
    )
    hits = top_k(q, mixed_catalog, k=20)
    ids = [h.id for h, _ in hits]
    assert "compiled_demoted_pid" not in ids


def test_include_demoted_flag(mixed_catalog) -> None:
    """include_demoted=True must re-include priority=-1 patterns."""
    q = RankerQuery(
        text="PID controller integral windup actuator saturation",
        task_family="pid_tuning",
    )
    hits = top_k(q, mixed_catalog, k=20, include_demoted=True)
    ids = [h.id for h, _ in hits]
    assert "compiled_demoted_pid" in ids


def test_min_score_filter(mixed_catalog) -> None:
    """min_score should filter out low-scoring matches."""
    q = RankerQuery(
        text="completely unrelated quantum chromodynamics",
    )
    hits = top_k(q, mixed_catalog, k=10, min_score=0.5)
    assert hits == [] or all(sb.total >= 0.5 for _, sb in hits)


# ── integration: load real Sprint-5 pattern manifest ─────────────────────


REPO = Path("/root/workspace/rosclaw/rosclaw_wiki/rosclaw-know")
MANIFEST = REPO / "data/assets/pattern_cards_v2.yaml"
HAS_MANIFEST = MANIFEST.is_file()


@pytest.mark.skipif(not HAS_MANIFEST, reason="run scripts/build_physical_graph.py first")
def test_pid_query_against_real_compiled_catalog() -> None:
    raw = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    cards = [PatternCardV2.model_validate(c) for c in raw["pattern_cards"]]
    q = RankerQuery(
        text="PID controller actuator saturation integral windup",
        task_family="robotics_optimization",
        embodiment_type="uav",
    )
    hits = top_k(q, cards, k=5)
    assert hits, "no hits returned from real catalog"
    # Top result must be PID/clamp related (Control_Locomotion or contains windup/clamp)
    top_id = hits[0][0].id
    assert (
        "windup" in top_id
        or "clamp" in top_id
        or hits[0][0].domain == "Control_Locomotion"
    ), f"unexpected top result: {top_id}"
