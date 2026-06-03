"""Frontier-Eng / Arena benchmark → :class:`TaskCard` extractor.

Sprint 2 deliverable (v1.5 plan §5.2, §11.3).

The extractor reads a single Frontier-Eng task directory and produces
one fully-typed :class:`rosclaw_know.schemas.TaskCard`.  It is purely
structural — no LLM call is required, every field is derived from the
files that ship inside the benchmark itself.

A leaf task directory looks like::

    benchmarks/<Family>/<TaskName>/
        Task.md                       — human-readable spec
        frontier_eval/
            initial_program.txt       — path to the editable artefact
            eval_command.txt          — how the evaluator is run
            constraints.txt           — UnifiedTask hard constraints
            evaluator.py              — the verifier itself
        scripts/init.py | baseline/*  — the candidate artefact
        verification/                 — evaluator + reference impl

Mapping
-------

The extractor maps that into the v1.5 TaskCard fields like this:

============================  ================================================
TaskCard field                Source
============================  ================================================
``id``                        ``f"task_{family}_{task_name}"`` (snake_case)
``benchmark``                 always ``"frontier-eng"`` here
``task_name``                 leaf directory name verbatim
``task_family``               ``f"{family_snake}_optimization"``
``domain``                    ``FAMILY_TO_DOMAIN`` lookup (FRONTIER_DOMAINS)
``artifact_type``             extension of ``initial_program.txt`` file path
``objective_direction``       Task.md scan: "higher is better" / "minimize" …
``metric_name``               first match in ``METRIC_PATTERNS``
``hard_constraints``          numbered bullets from constraints.txt + Task.md
``verifier_type``             text heuristic: GPU/popcorn → benchmark_harness …
``baseline_description``      first paragraph of ``## Problem`` section
``common_failure_modes``      ``FAMILY_RECOMMENDATIONS`` lookup
``recommended_patterns``      ``FAMILY_RECOMMENDATIONS`` lookup
============================  ================================================

Parent-index Task.md files (``benchmarks/EngDesign/Task.md``,
``benchmarks/MolecularMechanics/Task.md``) are skipped because the real
tasks live in sub-directories.

Stability guarantee:
    Given the same input files, the extractor produces a byte-identical
    TaskCard.  Tests rely on this — no randomness, no clock reads.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from rosclaw_know.schemas import (
    ArtifactType,
    ObjectiveDirection,
    TaskCard,
    VerifierType,
)

logger = logging.getLogger(__name__)


# ── family → FRONTIER_DOMAINS map ───────────────────────────────────────
#
# The seven Frontier-Eng domains (perception / planning / control /
# learning / memory / systems / world_physics) are coarse on purpose —
# many benchmark families bucket into "Systems_Compute" or
# "World_Physics" because they are fundamentally numerical-optimization
# tasks under physical or computational constraints.  See plan §3.

FAMILY_TO_DOMAIN: dict[str, str] = {
    # ── Control_Locomotion ──
    "Robotics":                       "Control_Locomotion",
    "Astrodynamics":                  "Control_Locomotion",
    "AdditiveManufacturing":          "Control_Locomotion",
    "PowerSystems":                   "Control_Locomotion",
    "SustainableDataCenterControl":   "Control_Locomotion",
    # ── Systems_Compute ──
    "KernelEngineering":              "Systems_Compute",
    "ComputerSystems":                "Systems_Compute",
    "Cryptographic":                  "Systems_Compute",
    "ElectronicDesignAutomation":     "Systems_Compute",
    # ── World_Physics ──
    "QuantumComputing":               "World_Physics",
    "ParticlePhysics":                "World_Physics",
    "MolecularMechanics":             "World_Physics",
    "Optics":                         "World_Physics",
    "EnergyStorage":                  "World_Physics",
    "CommunicationEngineering":       "World_Physics",
    "WirelessChannelSimulation":      "World_Physics",
    "Aerodynamics":                   "World_Physics",
    "StructuralOptimization":         "World_Physics",
    "ReactionOptimisation":           "World_Physics",
    # ── Learning_Training ──
    "SingleCellAnalysis":             "Learning_Training",
    # ── Planning_Decision ──
    "InventoryOptimization":          "Planning_Decision",
    "JobShop":                        "Planning_Decision",
    "PyPortfolioOpt":                 "Planning_Decision",
    "EngDesign":                      "Planning_Decision",
}


# ── per-family failure-mode and pattern recommendation ─────────────────
#
# These are deliberately conservative: only suggest a failure_mode /
# pattern that demonstrably applies to the family. Sprint 3+
# (trajectory mining) will expand this map from real run data.

FAMILY_RECOMMENDATIONS: dict[str, dict[str, list[str]]] = {
    # ── Control_Locomotion ──
    "Robotics": {
        "common_failure_modes": [
            "failure_pid_integrator_windup",
            "failure_actuator_output_unbounded",
            "failure_actuator_clamp_missing",
        ],
        "recommended_patterns": [
            "anti_windup_pid",
            "output_saturation_clamp",
            "closed_loop_replanning",
        ],
    },
    "Astrodynamics": {
        "common_failure_modes": [
            "failure_actuator_output_unbounded",
            "failure_planning_divergence",
        ],
        "recommended_patterns": [
            "output_saturation_clamp",
        ],
    },
    "AdditiveManufacturing": {
        "common_failure_modes": [
            "failure_actuator_output_unbounded",
            "failure_gradient_explosion",
        ],
        "recommended_patterns": [
            "output_saturation_clamp",
            "gradient_clipping",
        ],
    },
    "PowerSystems": {
        "common_failure_modes": [
            "failure_actuator_output_unbounded",
            "failure_planning_divergence",
        ],
        "recommended_patterns": [
            "output_saturation_clamp",
            "closed_loop_replanning",
        ],
    },
    "SustainableDataCenterControl": {
        "common_failure_modes": [
            "failure_planning_divergence",
        ],
        "recommended_patterns": [
            "closed_loop_replanning",
            "exponential_backoff_retry",
        ],
    },
    # ── Systems_Compute ──
    "KernelEngineering": {
        "common_failure_modes": [
            "failure_simulator_compile_failure",
        ],
        "recommended_patterns": [],
    },
    "Cryptographic": {
        "common_failure_modes": [
            "failure_simulator_compile_failure",
        ],
        "recommended_patterns": [],
    },
    "ComputerSystems": {
        "common_failure_modes": [
            "failure_simulator_compile_failure",
        ],
        "recommended_patterns": [],
    },
    "ElectronicDesignAutomation": {
        "common_failure_modes": [
            "failure_simulator_compile_failure",
            "failure_planning_divergence",
        ],
        "recommended_patterns": [],
    },
    # ── World_Physics ──
    # All bucketed similarly: numerical-optimization tasks where the
    # most common failure modes are constraint violation, gradient
    # blow-up, or simulator divergence.
    "Optics": {
        "common_failure_modes": [
            "failure_actuator_output_unbounded",
            "failure_gradient_explosion",
        ],
        "recommended_patterns": [
            "output_saturation_clamp",
            "gradient_clipping",
        ],
    },
    "Aerodynamics": {
        "common_failure_modes": [
            "failure_gradient_explosion",
            "failure_simulator_compile_failure",
        ],
        "recommended_patterns": [
            "gradient_clipping",
        ],
    },
    "CommunicationEngineering": {
        "common_failure_modes": [
            "failure_gradient_explosion",
            "failure_planning_divergence",
        ],
        "recommended_patterns": [
            "gradient_clipping",
        ],
    },
    "EnergyStorage": {
        "common_failure_modes": [
            "failure_actuator_output_unbounded",
            "failure_gradient_explosion",
        ],
        "recommended_patterns": [
            "output_saturation_clamp",
            "gradient_clipping",
        ],
    },
    "MolecularMechanics": {
        "common_failure_modes": [
            "failure_gradient_explosion",
        ],
        "recommended_patterns": [
            "gradient_clipping",
        ],
    },
    "ParticlePhysics": {
        "common_failure_modes": [
            "failure_gradient_explosion",
            "failure_planning_divergence",
        ],
        "recommended_patterns": [
            "gradient_clipping",
        ],
    },
    "QuantumComputing": {
        "common_failure_modes": [
            "failure_simulator_compile_failure",
            "failure_planning_divergence",
        ],
        "recommended_patterns": [],
    },
    "ReactionOptimisation": {
        "common_failure_modes": [
            "failure_gradient_explosion",
            "failure_planning_divergence",
        ],
        "recommended_patterns": [
            "gradient_clipping",
        ],
    },
    "StructuralOptimization": {
        "common_failure_modes": [
            "failure_gradient_explosion",
            "failure_actuator_output_unbounded",
        ],
        "recommended_patterns": [
            "gradient_clipping",
            "output_saturation_clamp",
        ],
    },
    "WirelessChannelSimulation": {
        "common_failure_modes": [
            "failure_simulator_compile_failure",
            "failure_gradient_explosion",
        ],
        "recommended_patterns": [
            "gradient_clipping",
        ],
    },
    # ── Learning_Training ──
    "SingleCellAnalysis": {
        "common_failure_modes": [
            "failure_gradient_explosion",
            "failure_ppo_entropy_collapse",
        ],
        "recommended_patterns": [
            "gradient_clipping",
            "ppo_entropy_collapse_guard",
        ],
    },
    # ── Planning_Decision ──
    "JobShop": {
        "common_failure_modes": [
            "failure_planning_divergence",
        ],
        "recommended_patterns": [
            "closed_loop_replanning",
        ],
    },
    "InventoryOptimization": {
        "common_failure_modes": [
            "failure_planning_divergence",
        ],
        "recommended_patterns": [
            "closed_loop_replanning",
        ],
    },
    "PyPortfolioOpt": {
        "common_failure_modes": [
            "failure_planning_divergence",
            "failure_gradient_explosion",
        ],
        "recommended_patterns": [
            "gradient_clipping",
        ],
    },
    "EngDesign": {
        "common_failure_modes": [
            "failure_planning_divergence",
        ],
        "recommended_patterns": [],
    },
}


# ── filename ext → ArtifactType ────────────────────────────────────────
#
# C files bucket into "cpp" since the TaskCard ArtifactType literal only
# admits {python, cpp, cuda, triton, yaml, params, rosbag, urdf} (see
# schemas.py).  cpp is the closest match for C kernels.

ART_EXT: dict[str, ArtifactType] = {
    ".py":   "python",
    ".cpp":  "cpp",
    ".cc":   "cpp",
    ".cxx":  "cpp",
    ".c":    "cpp",
    ".h":    "cpp",
    ".hpp":  "cpp",
    ".cu":   "cuda",
    ".cuh":  "cuda",
    ".yaml": "yaml",
    ".yml":  "yaml",
    ".json": "params",
}


# ── metric-name detection patterns (regex, ordered) ─────────────────────
#
# First match wins.  The patterns are intentionally narrow so a noisy
# Task.md doesn't pick up the wrong metric.

METRIC_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"score_0_to_1_higher_is_better", "score_0_to_1"),
    (r"\bcombined_score\b",              "combined_score"),
    (r"\bmakespan\b",                    "makespan"),
    (r"\bscore_best\b",                  "score_best"),
    (r"\bscore_lb\b",                    "score_lb"),
    (r"\bthroughput\b",                  "throughput"),
    (r"\bgeom_mean_ns\b",                "geom_mean_ns"),
    (r"\bITAE\b",                        "itae"),
    (r"\bBER\b",                         "ber"),
    (r"\bmean_rms\b",                    "mean_rms"),
    (r"\bmean_strehl\b",                 "mean_strehl"),
    (r"\bgate_count\b",                  "gate_count"),
)


# ── known metric → direction ────────────────────────────────────────────
#
# Several Frontier-Eng tasks tell a confusing story in prose ("minimize
# execution time" but score = 1e9 / time → maximize).  Anchor on the
# metric itself rather than risk picking up the wrong direction from
# free-form text.  Metrics not listed here fall back to the prose-scan
# heuristic in :func:`_detect_objective_direction`.

METRIC_DIRECTION: dict[str, ObjectiveDirection] = {
    "score_0_to_1":     "maximize",
    "combined_score":   "maximize",   # combined_score is always higher-better
    "score_best":       "maximize",
    "score_lb":         "maximize",
    "throughput":       "maximize",
    "mean_strehl":      "maximize",
    "makespan":         "minimize",
    "geom_mean_ns":     "minimize",
    "itae":             "minimize",
    "ber":              "minimize",
    "mean_rms":         "minimize",
    "gate_count":       "minimize",
}


# ── dataclass ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExtractInput:
    """Resolved file pointers for one task directory.

    Letting callers pre-resolve files means the extractor is fully
    testable without touching the filesystem.  ``load_task_dir`` is the
    convenience constructor for filesystem-backed extraction.
    """

    task_dir: Path
    family: str
    task_md_text: str | None
    initial_program: str | None
    eval_command: str | None
    constraints_text: str | None


# ── helpers ────────────────────────────────────────────────────────────


def _camel_to_snake(name: str) -> str:
    """``CarAerodynamicsSensing`` → ``car_aerodynamics_sensing``.

    Used for canonical id construction.  Dashes also collapse to
    underscore so ``AES-128`` becomes ``aes_128``.
    """
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower().replace("-", "_")


def _detect_artifact_type(initial_program: str | None, task_md_text: str) -> ArtifactType:
    """Resolve the editable artefact's language.

    ``frontier_eval/initial_program.txt`` is canonical when present —
    it spells out the artefact path (e.g. ``baseline/AES-128.cpp``).
    Falls back to text-scan for ``CUDA`` / ``C++`` / ``triton`` hints.
    """
    if initial_program:
        ip = initial_program.strip().splitlines()[0] if initial_program else ""
        if "." in ip:
            suffix = "." + ip.rsplit(".", 1)[-1].lower().rstrip()
            if suffix in ART_EXT:
                return ART_EXT[suffix]
    lo = task_md_text.lower()
    if "triton" in lo:
        return "triton"
    if "cuda" in lo or ".cu" in lo:
        return "cuda"
    if "c++" in lo or ".cpp" in lo:
        return "cpp"
    return "python"


def _detect_objective_direction(
    task_md_text: str, metric_name: str
) -> ObjectiveDirection:
    """Resolve direction.

    Strategy: anchor on the metric first (most metrics have a known
    convention — makespan → minimize, throughput → maximize).  Fall back
    to a prose scan for metrics that aren't on the known list.

    The prose scan itself has two phases — explicit direction phrases
    first, implicit cues second — but only fires when the metric is
    ``"combined_score"`` (i.e. our heuristic fallback metric, which on
    its own doesn't fix direction).
    """
    # Phase 0: known metric → known direction.  Authoritative.
    if metric_name in METRIC_DIRECTION:
        return METRIC_DIRECTION[metric_name]

    lo = task_md_text.lower()
    # Phase 1: explicit phrases
    if re.search(r"higher\s+is\s+better|obtain\s+higher\s+scores|maximize|maximi[sz]e", lo):
        return "maximize"
    if re.search(r"lower\s+is\s+better|minimize|minimi[sz]e", lo):
        return "minimize"
    # Phase 2: implicit cues
    if any(
        kw in lo
        for kw in (
            "makespan", "error rate", "latency", "runtime",
            "execution time", "wall time", "cost function",
        )
    ):
        return "minimize"
    if any(kw in lo for kw in ("throughput", "score is", "rank by")):
        return "maximize"
    return "maximize"


def _detect_metric_name(task_md_text: str) -> str:
    """First ``METRIC_PATTERNS`` hit wins; default ``combined_score``."""
    for pat, name in METRIC_PATTERNS:
        if re.search(pat, task_md_text):
            return name
    return "combined_score"


def _detect_verifier_type(
    eval_command: str | None,
    initial_program: str | None,
    task_md_text: str,
) -> VerifierType:
    """Classify the verifier from runner / artifact / spec text.

    Heuristic only — covers the four Frontier-Eng-relevant cases.
    ``simulator`` and ``benchmark_harness`` are the load-bearing ones
    for Sprint 2 (the rest of the corpus mostly uses ``checker_script``).
    """
    blob = (
        task_md_text + " "
        + (eval_command or "") + " "
        + (initial_program or "")
    ).lower()

    if "popcorn" in blob or "leaderboard" in blob or "gpu" in blob \
            or "kernel" in blob or "cuda" in blob or "triton" in blob:
        return "benchmark_harness"
    if "simulator" in blob or "rollout" in blob or "scenario" in blob \
            or "wind" in blob or "dynamics" in blob:
        return "simulator"
    if (
        "evaluate.py" in blob
        or "evaluator.py" in blob
        or "eval.py" in blob
        or "verifier" in blob
    ):
        return "checker_script"
    if "unit test" in blob or "pytest" in blob:
        return "unit_test"
    return "checker_script"


def _extract_hard_constraints(
    constraints_text: str | None,
    task_md_text: str,
) -> list[str]:
    """Pull numbered or bulleted constraints (capped at 12).

    Priority:

    1. ``frontier_eval/constraints.txt`` numbered list — the canonical
       UnifiedTask constraint enumeration.
    2. ``frontier_eval/constraints.txt`` bare-prose form — every
       non-empty non-comment line counts as one constraint (used by
       JobShop, which doesn't number).
    3. Task.md ``## Feasibility Rules`` / ``## Constraints`` section
       bullets.
    """
    out: list[str] = []

    def _strip_prefix(line: str) -> str:
        return re.sub(r"^\s*(?:\d+[\.\)]\s+|-\s+|\*\s+)", "", line).strip()

    def _is_bullet(line: str) -> bool:
        return bool(re.match(r"^\d+[\.\)]\s", line) or re.match(r"^[-*]\s", line))

    if constraints_text:
        # Phase 1: numbered/bulleted form
        for raw in constraints_text.splitlines():
            line = raw.strip()
            if _is_bullet(line):
                clean = _strip_prefix(line)
                if clean:
                    out.append(clean)

        # Phase 2: bare prose form, only when no bullets were found
        if not out:
            for raw in constraints_text.splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                # First line is often a header — skip if it's a label
                # ending in colon.
                if line.endswith(":") and len(line) < 80:
                    continue
                out.append(line)

    if not out and task_md_text:
        m = re.search(
            r"##\s*(?:\d+\.\s*)?(?:Feasibility Rules|Hard Constraints|"
            r"Constraints|Correctness Rule|Feasibility)\b.*?"
            r"(?=\n##\s|\Z)",
            task_md_text,
            re.DOTALL | re.IGNORECASE,
        )
        if m:
            for raw in m.group(0).splitlines():
                line = raw.strip()
                if _is_bullet(line):
                    clean = _strip_prefix(line)
                    if clean and not clean.lower().startswith(("note", "see ")):
                        out.append(clean)

    # Dedup preserving order, then cap.
    seen: set[str] = set()
    deduped: list[str] = []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped[:12]


def _extract_baseline_description(task_md_text: str) -> str:
    """Pull the first non-trivial paragraph as a 1-line summary.

    Strategy: find ``## Problem`` / ``## 1. Problem`` /
    ``## Background`` section, take the first non-blank line below the
    heading. Falls back to the first non-heading line in the file.
    """
    if not task_md_text:
        return ""

    m = re.search(
        r"##\s*(?:\d+\.\s*)?(?:Problem|Background|Task Description|"
        r"Task:|Audience|Overview)\b.*?\n",
        task_md_text,
        re.IGNORECASE,
    )
    if m:
        body = task_md_text[m.end():]
        for line in body.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped[:240]

    for line in task_md_text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:240]
    return ""


# ── core extractor ─────────────────────────────────────────────────────


def extract_task_card(inp: ExtractInput) -> TaskCard:
    """Build a fully-typed :class:`TaskCard` from resolved task files.

    Raises:
        pydantic.ValidationError: if the constructed card fails the
            schema (e.g. unknown domain, bad artifact_type).  Callers
            that want a "best-effort" sweep should catch and skip.
    """
    family = inp.family
    task_name = inp.task_dir.name
    domain = FAMILY_TO_DOMAIN.get(family, "Systems_Compute")
    task_md_text = inp.task_md_text or ""

    artifact_type = _detect_artifact_type(inp.initial_program, task_md_text)
    metric_name = _detect_metric_name(task_md_text)
    objective_direction = _detect_objective_direction(task_md_text, metric_name)
    verifier_type = _detect_verifier_type(
        inp.eval_command, inp.initial_program, task_md_text
    )
    hard_constraints = _extract_hard_constraints(inp.constraints_text, task_md_text)
    baseline_description = _extract_baseline_description(task_md_text)

    rec = FAMILY_RECOMMENDATIONS.get(family, {})
    common_failure_modes = list(rec.get("common_failure_modes", []))
    recommended_patterns = list(rec.get("recommended_patterns", []))

    # Canonical id: task_<family_snake>_<task_snake>
    family_snake = _camel_to_snake(family)
    task_snake = _camel_to_snake(task_name)
    card_id = f"task_{family_snake}_{task_snake}".replace("__", "_")

    return TaskCard(
        id=card_id,
        benchmark="frontier-eng",
        task_name=task_name,
        task_family=f"{family_snake}_optimization",
        domain=domain,
        artifact_type=artifact_type,
        objective_direction=objective_direction,
        metric_name=metric_name,
        hard_constraints=hard_constraints,
        verifier_type=verifier_type,
        baseline_description=baseline_description,
        common_failure_modes=common_failure_modes,
        recommended_patterns=recommended_patterns,
    )


# ── filesystem driver ─────────────────────────────────────────────────


def load_task_dir(task_dir: Path) -> ExtractInput | None:
    """Read all relevant files for ``task_dir``.

    Returns ``None`` if there's no ``Task.md`` (i.e. not a task dir).
    The family is inferred from the parent directory name —
    ``benchmarks/Robotics/PIDTuning`` → ``Robotics``.
    """
    task_md = task_dir / "Task.md"
    if not task_md.is_file():
        return None

    family = task_dir.parent.name
    fe_dir = task_dir / "frontier_eval"

    def _read_opt(p: Path) -> str | None:
        try:
            return p.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            return None

    return ExtractInput(
        task_dir=task_dir,
        family=family,
        task_md_text=_read_opt(task_md),
        initial_program=_read_opt(fe_dir / "initial_program.txt"),
        eval_command=_read_opt(fe_dir / "eval_command.txt"),
        constraints_text=_read_opt(fe_dir / "constraints.txt"),
    )


def is_parent_index(task_dir: Path) -> bool:
    """True if ``task_dir`` is a TOC/index rather than a leaf task.

    Heuristics — any of these flag a parent dir:

    * Has a child subdir with its own ``Task.md`` (e.g. ``MolecularMechanics``)
    * Has a child subdir with ``LLM_prompt.txt`` (e.g. ``EngDesign``)
    * Its ``Task.md`` body is a stub (under 400 chars of content)
    """
    try:
        for sub in task_dir.iterdir():
            if sub.is_dir() and (
                (sub / "Task.md").is_file()
                or (sub / "LLM_prompt.txt").is_file()
            ):
                return True
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        pass

    # Stub-length filter — EngDesign/Task.md is 3 lines.
    task_md = task_dir / "Task.md"
    try:
        if task_md.is_file() and len(task_md.read_text(encoding="utf-8")) < 400:
            return True
    except OSError:
        pass
    return False


def extract_from_corpus(benchmarks_root: Path) -> list[TaskCard]:
    """Walk a ``benchmarks/`` tree and emit one :class:`TaskCard` per
    leaf task.

    Order is deterministic: sorted by directory path so re-runs are
    byte-identical.  Skips parent-index Task.md files.

    Records but does not re-raise extraction failures — one malformed
    Task.md cannot kill a whole corpus sweep.  Caller can inspect logs
    for skipped entries.
    """
    cards: list[TaskCard] = []
    for task_md in sorted(benchmarks_root.rglob("Task.md")):
        task_dir = task_md.parent
        if is_parent_index(task_dir):
            continue
        inp = load_task_dir(task_dir)
        if inp is None:
            continue
        try:
            cards.append(extract_task_card(inp))
        except Exception as exc:
            logger.warning("failed to extract %s: %s", task_dir, exc)
    return cards
