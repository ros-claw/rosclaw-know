"""Typed Knowledge Objects for ROSClaw-Know v1.5.

Pydantic v2 schemas for every first-class knowledge artefact the
"Physical-AI knowledge compiler" produces.  Migrating away from the v1
``symptom → fix_pattern`` flat shape into a typed graph: each kind of
knowledge (failure / fix / constraint / task / embodiment / verifier /
evidence / source / pattern-card) has its own object so the downstream
graph_builder_v2, pattern_compiler_v2, hybrid retriever and evidence
distiller can reason over them without re-parsing strings.

Design notes
------------

* **Backwards-compatible.** All v2 metadata lives inside a new
  ``metadata`` block on each bridge cluster (see :class:`BridgeClusterV2`).
  The original v1 fields (``standard_name``, ``domain``,
  ``cross_domain_analogies``, ``associated_patterns``, ``priority``, …)
  stay exactly where rosclaw-how expects them.  How can continue to read
  v1 fields verbatim and ignore ``metadata`` until it opts in.

* **Strict where it matters.** ``domain`` must be one of
  :data:`FRONTIER_DOMAINS`; ``priority`` must be ``-1 | 0 | 1 | None``
  (None = legacy production); ``objective_direction`` must be
  ``"maximize" | "minimize"``.  Pydantic raises ValidationError on
  anything else — that's the schema validator the plan §11.1 asks for.

* **Forwards-compatible.** Every object carries
  ``schema_version: str`` so future format bumps can detect old payloads.

* **Frontier-Eng aware.** :class:`TaskCard` covers the artefact
  taxonomy the plan §4.1.5 calls out (python / cpp / cuda / triton /
  yaml / params) and the Frontier-Eng-relevant verifier types.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Re-export the canonical domain tuple so callers can do
# ``from rosclaw_know.schemas import FRONTIER_DOMAINS``.
from .prompts import FRONTIER_DOMAINS

SCHEMA_VERSION = "2.0"

# ── enumerations ────────────────────────────────────────────────────────

Severity = Literal["info", "warning", "safety_critical"]
"""How loud a FailureMode should ring when matched."""

Priority = Literal[-1, 0, 1]
"""``-1`` demoted · ``0`` staging · ``1`` production · ``None`` = legacy.

The plan §3.1 calls out that absent priority is treated as production
for backwards-compat.  We model that with ``int | None`` rather than
forcing every legacy entry to carry a value.
"""

ObjectiveDirection = Literal["maximize", "minimize"]

ConstraintType = Literal[
    "safety",   # actuator limits, joint limits, collision margin
    "physics",  # energy conservation, force balance, contact dynamics
    "numerical",  # bounded gradient norm, condition number, NaN guard
    "resource",  # GPU mem, latency, throughput, bandwidth
    "protocol",  # message format, handshake, ordering
    "task",     # task-specific (returncode == 0, valid output schema)
]

EmbodimentType = Literal[
    "manipulator",     # arm: UR5, KUKA, xArm
    "quadruped",       # Go2, Spot, Ant
    "humanoid",        # G1, H1, Atlas
    "wheeled_robot",   # TurtleBot, AGV
    "uav",             # quadrotor, fixed-wing
    "gpu_kernel",      # CUDA / Triton kernels
    "data_center",     # batch scheduler, load balancer
    "battery",         # battery management system
    "optical_system",  # camera, LiDAR, sensor pipeline
]

ArtifactType = Literal[
    "python", "cpp", "cuda", "triton", "yaml", "params", "rosbag", "urdf",
]

VerifierType = Literal[
    "unit_test",          # runs unit tests in a sandbox
    "simulator",          # MuJoCo / PyBullet / Isaac / Gazebo rollout
    "checker_script",     # bespoke evaluator that returns score
    "real_hardware",      # logs from a physical robot
    "static_analysis",    # AST / type / lint check
    "benchmark_harness",  # Frontier-Eng style harness
]

SourceQualityLevel = Literal[
    "S",  # ROSClaw self-verified via verifier
    "A",  # paper + reproducible code
    "B",  # high-quality GitHub repo / official docs
    "C",  # tutorial / blog / awesome list
    "D",  # autodraft / LLM draft
]

LifecycleStatus = Literal[
    "needs_validation",   # legacy unbucketed, waiting for feedback
    "staging",            # priority=0
    "production",         # priority=1
    "demoted",            # priority=-1
]

# ── shared base ─────────────────────────────────────────────────────────


class _Base(BaseModel):
    """Common config: forbid unknown fields by default (fail-loud schema)."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


# ── 1. FailureMode (plan §4.1.1) ────────────────────────────────────────


class FailureMode(_Base):
    """One named way a physical-AI system can break.

    A FailureMode is the v1 *symptom* upgraded with structural
    metadata: what signals an operator would see, what the likely
    causes are, what NOT to do, and how severe an occurrence is.
    """

    id: str = Field(pattern=r"^failure_[a-z0-9_]+$")
    name: str
    domain: str
    symptom_text: str
    normalized_symptom: str
    observable_signals: list[str] = Field(default_factory=list)
    likely_causes: list[str] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    severity: Severity = "warning"
    schema_version: str = SCHEMA_VERSION

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, v: str) -> str:
        if v not in FRONTIER_DOMAINS:
            raise ValueError(
                f"domain {v!r} not in FRONTIER_DOMAINS={FRONTIER_DOMAINS}"
            )
        return v


# ── 2. FixPattern (plan §4.1.2) ─────────────────────────────────────────


class FixPattern(_Base):
    """A reusable engineering fix for one or more FailureModes."""

    id: str
    failure_ids: list[str] = Field(default_factory=list)
    domain: str
    fix_summary: str
    preconditions: list[str] = Field(default_factory=list)
    implementation_steps: list[str] = Field(default_factory=list)
    code_targets: list[str] = Field(default_factory=list)
    expected_verifier_signals: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, v: str) -> str:
        if v not in FRONTIER_DOMAINS:
            raise ValueError(f"domain {v!r} not in FRONTIER_DOMAINS")
        return v


# ── 3. ConstraintPattern (plan §4.1.3) ──────────────────────────────────


class ConstraintPattern(_Base):
    """A hard or soft constraint the agent must respect.

    Constraints are *not* fixes — they are the rules of the road that a
    fix must not violate (e.g. "do not exceed torque_max", "memory must
    not exceed 2GB").  The hybrid retriever uses them to filter
    candidate FixPatterns.
    """

    id: str
    constraint_type: ConstraintType
    description: str
    check_method: str
    violation_signals: list[str] = Field(default_factory=list)
    repair_strategies: list[str] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION


# ── 4. EmbodimentCard (plan §4.1.4) ─────────────────────────────────────


class EmbodimentCard(_Base):
    """Static description of a physical (or virtual) embodiment.

    Lets the retriever boost patterns that apply to the robot the agent
    is actually controlling.  e.g. anti-windup applies to manipulators
    and quadrotors, but not directly to scheduling jobs.
    """

    id: str
    embodiment_type: EmbodimentType
    sensors: list[str] = Field(default_factory=list)
    actuators: list[str] = Field(default_factory=list)
    control_interfaces: list[str] = Field(default_factory=list)
    common_failures: list[str] = Field(default_factory=list)
    simulators: list[str] = Field(default_factory=list)
    safety_constraints: list[str] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION


# ── 5. TaskCard (plan §4.1.5) ───────────────────────────────────────────


class TaskCard(_Base):
    """Frontier-Eng / ROSClaw Arena task descriptor.

    The single most important new object for v1.5: the agent needs to
    know what kind of task it's about to attempt *before* starting, so
    it can pull in the right priors.  TaskCards are also what
    :func:`task_pack_builder.build` consumes.
    """

    id: str
    benchmark: str | None = None
    task_name: str
    task_family: str
    domain: str
    artifact_type: ArtifactType
    objective_direction: ObjectiveDirection
    metric_name: str
    hard_constraints: list[str] = Field(default_factory=list)
    verifier_type: VerifierType
    baseline_description: str = ""
    common_failure_modes: list[str] = Field(default_factory=list)
    recommended_patterns: list[str] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, v: str) -> str:
        if v not in FRONTIER_DOMAINS:
            raise ValueError(f"domain {v!r} not in FRONTIER_DOMAINS")
        return v


# ── 6. VerifierCard ─────────────────────────────────────────────────────


class VerifierCard(_Base):
    """How a task's success is measured at evaluation time.

    Separate from :class:`TaskCard` because one verifier can serve many
    tasks (e.g. one MuJoCo rollout harness covers all PID tuning tasks).
    """

    id: str
    verifier_type: VerifierType
    objective_direction: ObjectiveDirection
    metric_name: str
    score_range: tuple[float, float] | None = None
    expected_signals: list[str] = Field(default_factory=list)
    validity_checks: list[str] = Field(default_factory=list)
    runtime_estimate_seconds: float | None = None
    schema_version: str = SCHEMA_VERSION


# ── 7. EvidenceTrace (plan §8.1) ────────────────────────────────────────


class EvidenceTrace(_Base):
    """One observation of a pattern actually being used by an agent.

    The unit of evidence for :mod:`evidence_distill`: lets us measure
    placebo-adjusted uplift, hint-use rate, validity preservation, etc.
    Distinct from the v1 ``injection_outcomes`` JSONL by carrying the
    code-diff fingerprint and the actual hint features the agent picked
    up (or didn't).
    """

    trace_id: str
    run_id: str
    task_name: str
    iteration: int = Field(ge=0)
    injection_id: str | None = None
    pattern_id: str | None = None
    strategy: Literal["SAFETY", "FREE_EXPLORATION", "CATALYST", "NONE"]
    pre_score: float
    post_score_1: float | None = None
    post_score_3: float | None = None
    post_score_5: float | None = None
    best_delta_5: float | None = None
    code_diff_summary: list[str] = Field(default_factory=list)
    hint_features: list[str] = Field(default_factory=list)
    used_hint: bool = False
    verifier_status: Literal["valid", "invalid", "crashed", "unknown"] = "unknown"
    objective_direction: ObjectiveDirection
    arm: Literal["baseline", "true", "placebo", "shuffled"] = "true"
    timestamp: str = ""
    schema_version: str = SCHEMA_VERSION


# ── 8. SourceRecordV2 (plan §11.2) ──────────────────────────────────────


class SourceRecordV2(_Base):
    """Provenance of one corpus document.

    Upgraded from the v1 source_manifest: tracks license, commit hash,
    trust score and a graded quality level so the retriever can prefer
    higher-quality sources.
    """

    source_id: str
    source_type: Literal[
        "paper", "repo", "tutorial", "web", "benchmark", "robot_log",
        "verifier_output", "trajectory", "autodraft", "curated",
    ]
    source_quality: SourceQualityLevel
    url: str = ""
    license: str | None = None
    commit_hash: str | None = None
    retrieved_at: str = ""
    content_hash: str = ""
    trust_score: float = Field(default=0.5, ge=0.0, le=1.0)
    schema_version: str = SCHEMA_VERSION


# ── 9. PatternCardV2 (plan §7.1) ────────────────────────────────────────


class EvidenceBlock(_Base):
    """Aggregated evidence stats for one pattern."""

    n: int = Field(default=0, ge=0)
    avg_uplift: float = 0.0
    win_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    hint_use_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    last_seen: str = ""
    placebo_adjusted_uplift: float | None = None


class PatternCardV2(_Base):
    """Action-oriented pattern card the v1.5 compiler emits.

    Replaces the v1 free-form pattern markdown with structured fields
    the agent can render into a prompt.  The on-disk format keeps the
    markdown body for human readability; this class is the typed view.
    """

    id: str
    domain: str
    task_families: list[str] = Field(default_factory=list)
    embodiment_types: list[EmbodimentType] = Field(default_factory=list)
    artifact_languages: list[ArtifactType] = Field(default_factory=list)
    priority: Priority | None = None

    # Body sections (plan §7.1 mandatory)
    symptom: str
    diagnosis: str
    preconditions: list[str] = Field(default_factory=list)
    next_experiment: str
    code_target: str
    patch_sketch: str = ""
    expected_verifier_signals: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    cross_domain_analogy: str = ""

    # Metadata
    source_quality: SourceQualityLevel = "C"
    source_ids: list[str] = Field(default_factory=list)
    evidence: EvidenceBlock = Field(default_factory=EvidenceBlock)
    schema_version: str = SCHEMA_VERSION

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, v: str) -> str:
        if v not in FRONTIER_DOMAINS:
            raise ValueError(f"domain {v!r} not in FRONTIER_DOMAINS")
        return v


# ── 10. BridgeClusterV2 (plan §4.2) ─────────────────────────────────────


class ClusterMetadataV2(_Base):
    """The new ``metadata`` block bolted onto each v1 cluster.

    Lives at ``bridge_index.symptom_clusters[cid].metadata`` to preserve
    how's existing read path while letting v2 consumers do typed
    queries.
    """

    schema_version: str = SCHEMA_VERSION
    lifecycle_status: LifecycleStatus = "needs_validation"
    task_families: list[str] = Field(default_factory=list)
    embodiment_types: list[EmbodimentType] = Field(default_factory=list)
    artifact_languages: list[ArtifactType] = Field(default_factory=list)
    objective_directions: list[ObjectiveDirection] = Field(default_factory=list)
    verifier_signals: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    source_quality: SourceQualityLevel = "C"
    source_ids: list[str] = Field(default_factory=list)
    evidence: EvidenceBlock = Field(default_factory=EvidenceBlock)


class BridgeClusterV2(_Base):
    """One cluster in ``bridge_index.symptom_clusters`` (v1+v2 unified).

    All v1 fields stay top-level (how reads them as-is); v2 additions
    live under ``metadata``.  The migration in
    ``scripts/migrate_assets_v1_to_v2.py`` does exactly this: it never
    rewrites a top-level field, only injects ``metadata``.
    """

    standard_name: str
    domain: str
    matched_keywords: list[str] = Field(default_factory=list)
    cross_domain_analogies: list[dict[str, Any]] = Field(default_factory=list)
    associated_patterns: list[str] = Field(default_factory=list)
    priority: Priority | None = None
    is_staging: bool | None = None
    safety_label: str | None = None
    source: str | None = None
    uplift_mean: float | None = None
    uplift_n: int | None = None
    win_rate: float | None = None
    last_seen: str | None = None
    metadata: ClusterMetadataV2 | None = None

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, v: str) -> str:
        if v not in FRONTIER_DOMAINS:
            raise ValueError(f"domain {v!r} not in FRONTIER_DOMAINS")
        return v


# ── 11. BridgeIndexV2 ───────────────────────────────────────────────────


class BridgeIndexV2(_Base):
    """The full ``bridge_index.json`` document, v1-compatible + typed.

    Most callers should keep using ``json.load(...)`` and reach into the
    dict directly.  This class is for validation
    (:func:`validate_bridge`) and for code that benefits from a typed
    view.
    """

    symptom_clusters: dict[str, BridgeClusterV2] = Field(default_factory=dict)
    safety_label_index: dict[str, list[str]] = Field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    @field_validator("safety_label_index", mode="before")
    @classmethod
    def _normalize_safety_label_index(
        cls, v: dict[str, Any] | None
    ) -> dict[str, list[str]]:
        """Accept either ``str`` or ``list[str]`` values, normalize to list.

        Historical bridge_index files (Phase 1-7) store one curated
        pattern id per safety label as a bare string; later code paths
        and the plan §3.1 spec want a list.  Normalize on read so the
        downstream typed view is uniform without breaking on disk.
        """
        if v is None:
            return {}
        out: dict[str, list[str]] = {}
        for k, val in v.items():
            if isinstance(val, str):
                out[k] = [val]
            elif isinstance(val, list):
                out[k] = [str(x) for x in val]
            else:
                raise ValueError(
                    f"safety_label_index[{k!r}] must be str or list[str], got {type(val).__name__}"
                )
        return out


# ── 12. Trajectory / Mutation / CandidatePattern (Sprint 3, plan §5.3) ──
#
# How agent experience flows back into the knowledge base:
#
#     RunArtifact (one (task, model, algo, seed) tuple)
#         └── Trajectory (ordered list of TrajectoryStep)
#               └── TrajectoryStep (one iteration: code → eval result)
#
# The :class:`Mutation` class describes one *abstracted* change between
# two adjacent iterations.  Concrete numeric values are deliberately
# scrubbed (see ``code_diff_summarizer`` and tests) so we never leak the
# verbatim answer back into the knowledge base — see plan §3.5 on
# answer-leak prevention.
#
# :class:`CandidatePattern` is the Sprint 3 product: a tentative pattern
# extracted by the feature extractors.  It feeds Sprint 4's pattern
# compiler.

MutationKind = Literal[
    "set_parameter_zero",      # zero out an integral gain, weight, etc.
    "set_parameter_constant",  # any param ← literal
    "add_output_clamp",        # np.clip / max / min added on output
    "add_time_budget",         # explicit wall-clock budget guard
    "add_input_validation",    # type or range check at boundary
    "remove_assertion",        # debug-time guard removed
    "swap_optimizer",          # random search → CMA-ES / Bayesian etc.
    "vectorize_loop",          # explicit Python loop → numpy/array form
    "cache_repeated_call",     # memoize an expensive call
    "switch_algorithm_class",  # algorithm-family change
    "raise_iteration_count",   # n_iter ↑
    "lower_iteration_count",   # n_iter ↓
    "add_initialization_seed", # use prior-best as init
    "other",                   # anything not yet classified
]


class Mutation(_Base):
    """One abstracted change between two iterations.

    The :attr:`description` is the agent-facing string: it must NOT
    contain concrete numeric magnitudes that would let a downstream
    agent copy-paste the answer.  Use phrases like
    "set integral gain to zero" instead of "Ki_z = 0.142".  The
    abstraction guarantee is enforced by the code-diff summariser, with
    tests in ``test_trajectory_extractor.py``.
    """

    kind: MutationKind
    description: str
    target_identifier: str | None = None
    """The variable / function / module the mutation touched, if known.

    Stays at the symbol level — never carry the symbol's *value*.
    """
    score_delta: float | None = None
    """Score change associated with this mutation.

    Optional because some mutations are batched (i.e. several mutations
    landed before the next eval).
    """
    schema_version: str = SCHEMA_VERSION


class TrajectoryStep(_Base):
    """One iteration in a Trajectory: the code that was tried + its eval."""

    iteration: int
    score: float | None = None
    valid: bool = True
    """``False`` if the candidate failed feasibility (e.g. simulator
    crashed, returncode != 0, constraint violation).
    """
    mutations: list[Mutation] = Field(default_factory=list)
    """Mutations that distinguish this step from the *previous* step."""
    schema_version: str = SCHEMA_VERSION

    @field_validator("iteration")
    @classmethod
    def _check_iteration(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"iteration must be ≥ 0, got {v}")
        return v


class Trajectory(_Base):
    """An ordered sequence of TrajectoryStep observations for one run.

    Sourced from either:

    * Real ``iteration_NNN/code.{py,cpp}`` + ``iteration_NNN/eval.json``
      directories (the canonical format).
    * Degenerate single-step trajectories built from a
      ``baseline_archive/<task>/program.py`` final-best (see
      ``trajectory_extractor.from_baseline_archive``).
    """

    trajectory_id: str
    task_name: str
    benchmark: str | None = None
    algorithm: str | None = None
    """Search algorithm class (e.g. ``openevolve``, ``abmcts``)."""
    model: str | None = None
    """LLM model used in the agent loop."""
    steps: list[TrajectoryStep] = Field(default_factory=list)
    best_delta: float | None = None
    """``best_score - baseline_score`` — convenience field."""
    notes: list[str] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION


class CandidatePattern(_Base):
    """Tentative pattern extracted by Sprint 3 feature extractors.

    Sprint 4's pattern compiler turns these into real
    :class:`PatternCardV2` markdown files.  We keep them separate so
    candidate patterns can be reviewed before being elevated.
    """

    id: str = Field(pattern=r"^candidate_[a-z0-9_]+$")
    task_family: str
    """Where the pattern came from (e.g. ``robotics_optimization``)."""
    failure_id: str | None = None
    """Matching FailureMode id, if the extractor could identify one."""
    diagnosis: str
    successful_mutations: list[Mutation] = Field(default_factory=list)
    failed_mutations: list[Mutation] = Field(default_factory=list)
    expected_verifier_signal: str = ""
    contraindications: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    """How many independent trajectories produced this candidate.

    Plan §3.5: ``evidence_count >= 2`` is the lower bound for
    promotion in Sprint 4.
    """
    avg_score_delta: float | None = None
    source_trajectory_ids: list[str] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION


# ── 13. TaskPack (Sprint 7, plan §10) ────────────────────────────────────


class TaskPackQuery(_Base):
    """Sprint-7 input: what the agent is about to attempt.

    Plan §10.1 schema for ``POST /know/v1/task-pack/build``.  Most
    fields are optional — the builder fills them from the matched
    TaskCard when omitted.
    """

    task_name: str
    """The concrete task identifier (e.g. ``pid_tuning``, ``flash_attention``)."""
    benchmark: str | None = None
    """Benchmark family hint (``frontier-eng`` / ``arena`` / etc.)."""
    artifact_language: ArtifactType | None = None
    objective_direction: ObjectiveDirection | None = None
    metric_name: str | None = None
    budget_iterations: int = Field(default=20, ge=1, le=1_000_000)
    """Iteration budget the agent has — drives ``exploration_plan`` length."""
    top_k_patterns: int = Field(default=5, ge=1, le=50)
    """How many patterns to surface in :attr:`TaskPack.recommended_patterns`."""
    max_tokens: int = Field(default=1200, ge=200, le=8000)
    """Plan §Sprint 7 hard ceiling on the markdown render: ≤1200 tokens
    by default so it fits in an agent system prompt."""


class TaskPackPatternRef(_Base):
    """One pattern recommendation inside a :class:`TaskPack`.

    Carries the structured ``pattern_id`` reference the plan §Sprint 7
    acceptance ("task pack 引用 pattern_id，可追踪反馈") requires —
    rosclaw-how can match this back to its bridge_index entry to write
    a feedback EvidenceTrace.
    """

    pattern_id: str
    """Stable identifier (e.g. ``compiled_zero_integral_gain_on_saturation``)."""
    score: float | None = None
    """The hybrid-retriever score that surfaced this pattern."""
    reason: str = ""
    """One-line agent-facing description of why this pattern was picked."""
    domain: str | None = None


class TaskPack(_Base):
    """Plan §10 response object: agent's pre-flight task knowledge pack.

    Generated by :mod:`task_pack_builder` before the agent's first
    mutation, returned by ``POST /know/v1/task-pack/build`` and the
    ``rosclaw_task_pack`` MCP tool.  Order of fields matches the plan
    spec so agents can rely on stable structure.
    """

    task_pack_id: str
    """``<benchmark>_<task_family_or_name>_v<n>`` style id."""
    summary: str
    """Compressed task description (1-3 sentences)."""
    objective_direction: ObjectiveDirection
    metric_name: str
    hard_constraints: list[str] = Field(default_factory=list)
    recommended_patterns: list[TaskPackPatternRef] = Field(default_factory=list)
    exploration_plan: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
    expected_signals: list[str] = Field(default_factory=list)

    # ── provenance ──
    source_task_card_id: str | None = None
    source_failure_ids: list[str] = Field(default_factory=list)
    budget_iterations: int = 20
    token_estimate: int = 0
    """Word-count estimate of the rendered markdown — used as the gate
    against :attr:`TaskPackQuery.max_tokens`.  A word ≈ a token for the
    purposes of fitting an agent system prompt."""
    schema_version: str = SCHEMA_VERSION





def validate_bridge(data: dict[str, Any]) -> BridgeIndexV2:
    """Validate a raw ``bridge_index.json`` dict against the v2 schema.

    Raises ``pydantic.ValidationError`` on any structural problem.  This
    is the entry point the plan §11.1 calls "bridge_index schema
    validator" — wire it into CI before any deploy.
    """
    return BridgeIndexV2.model_validate(data)


__all__ = [
    "SCHEMA_VERSION",
    "FRONTIER_DOMAINS",
    "Severity",
    "Priority",
    "ObjectiveDirection",
    "ConstraintType",
    "EmbodimentType",
    "ArtifactType",
    "VerifierType",
    "SourceQualityLevel",
    "LifecycleStatus",
    "FailureMode",
    "FixPattern",
    "ConstraintPattern",
    "EmbodimentCard",
    "TaskCard",
    "VerifierCard",
    "EvidenceTrace",
    "SourceRecordV2",
    "EvidenceBlock",
    "PatternCardV2",
    "ClusterMetadataV2",
    "BridgeClusterV2",
    "BridgeIndexV2",
    "MutationKind",
    "Mutation",
    "TrajectoryStep",
    "Trajectory",
    "CandidatePattern",
    "TaskPack",
    "TaskPackPatternRef",
    "TaskPackQuery",
    "validate_bridge",
]
