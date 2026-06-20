"""Deterministic TaskCard v1 compiler."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .failure_taxonomy import Failure, FailureCategory, FailureTaxonomy
from .physical_constraints import (
    HardConstraint,
    OperationalConstraint,
    PhysicalConstraints,
)
from .recipes import RECIPES, TaskRecipe
from .schemas import (
    AutoHooks,
    CognitiveWikiSync,
    EmbodimentProfile,
    EngineeringPrior,
    EvidenceItem,
    ExperimentCandidate,
    HowHooks,
    MemoryHooks,
    MemoryQuery,
    QualityScores,
    SceneObject,
    SceneProfile,
    SceneUncertainty,
    Subtask,
    TaskCard,
    TaskGoal,
    TaskMetadata,
    WikiCardRef,
)
from .schemas import (
    HowTrigger as SchemaHowTrigger,
)


class TaskCardCompileError(Exception):
    """Raised when a task cannot be compiled into a valid TaskCard."""


_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _data_file(*parts: str) -> Path:
    return _DATA_DIR.joinpath(*parts)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_failure_taxonomy(taxonomy_name: str = "common_robot_failures") -> FailureTaxonomy:
    data = _load_yaml(_data_file("failure_taxonomy", f"{taxonomy_name}.yaml"))
    if not data:
        return FailureTaxonomy(categories=[], failures=[])
    return FailureTaxonomy.model_validate(data)


def _load_physical_constraints(constraints_name: str = "humanoid_constraints") -> PhysicalConstraints:
    data = _load_yaml(_data_file("physical_constraints", f"{constraints_name}.yaml"))
    if not data:
        return PhysicalConstraints()
    return PhysicalConstraints.model_validate(data)


def _resolve_scene(
    recipe: TaskRecipe,
    scene_path: str | None,
    scene_id: str | None,
) -> SceneProfile:
    objects = [SceneObject(id=oid, type=otype, properties=props) for oid, otype, props in recipe.required_scene_objects]
    uncertainties = [
        SceneUncertainty(id=uid, description=desc, expected_range=rng)
        for uid, desc, rng in recipe.scene_uncertainty
    ]

    if scene_path:
        return SceneProfile(
            scene_id=scene_id or f"{recipe.task_id}_scene",
            scene_type="indoor_lab" if recipe.embodiment_type == "humanoid" else "unknown",
            scene_ref=scene_path,
            objects=objects,
            environmental_assumptions=recipe.environmental_assumptions,
            uncertainty=uncertainties,
        )

    assumptions = list(recipe.environmental_assumptions)
    assumptions.append("未提供场景文件，使用默认假设")
    return SceneProfile(
        scene_id="unknown",
        scene_type="unknown",
        scene_ref=None,
        objects=objects,
        environmental_assumptions=assumptions,
        uncertainty=uncertainties,
    )


def _build_failure_taxonomy(recipe: TaskRecipe) -> FailureTaxonomy:
    common = _load_failure_taxonomy("common_robot_failures")

    categories_by_id: dict[str, FailureCategory] = {c.id: c for c in common.categories}
    failures_by_id: dict[str, Failure] = {f.id: f for f in common.failures}

    for raw in recipe.additional_failures:
        failure = Failure.model_validate(raw)
        failures_by_id[failure.id] = failure
        if failure.category not in categories_by_id:
            categories_by_id[failure.category] = FailureCategory(
                id=failure.category,
                name=failure.category,
                severity_default="S2",
                description="",
            )

    needed_ids: set[str] = set()
    for st in recipe.subtasks:
        needed_ids.update(st.likely_failures)

    selected_failures = [failures_by_id[fid] for fid in needed_ids if fid in failures_by_id]
    selected_categories = {f.category for f in selected_failures}

    return FailureTaxonomy(
        categories=[categories_by_id[cid] for cid in sorted(selected_categories) if cid in categories_by_id],
        failures=selected_failures,
    )


def _build_physical_constraints(recipe: TaskRecipe) -> PhysicalConstraints:
    constraints = PhysicalConstraints()

    if recipe.embodiment_type == "humanoid":
        humanoid = _load_physical_constraints("humanoid_constraints")
        constraints.hard_constraints.extend(humanoid.hard_constraints)
        constraints.soft_constraints.extend(humanoid.soft_constraints)
        constraints.operational_constraints.extend(humanoid.operational_constraints)
        constraints.context_constraints.extend(humanoid.context_constraints)

    for raw in recipe.additional_constraints:
        if raw.get("check"):
            constraints.hard_constraints.append(
                HardConstraint.model_validate(raw)
            )
        else:
            constraints.operational_constraints.append(
                OperationalConstraint.model_validate(raw)
            )

    return constraints


def _build_subtasks(recipe: TaskRecipe) -> list[Subtask]:
    return [
        Subtask(
            id=st.id,
            name=st.name,
            phase=st.phase,
            description=st.description,
            inputs=st.inputs,
            outputs=st.outputs,
            required_capabilities=st.required_capabilities,
            success_criteria=st.success_criteria,
            likely_failures=st.likely_failures,
        )
        for st in recipe.subtasks
    ]


def _build_priors(recipe: TaskRecipe) -> list[EngineeringPrior]:
    return [EngineeringPrior.model_validate(p) for p in recipe.engineering_priors]


def _build_memory_hooks(recipe: TaskRecipe) -> MemoryHooks:
    queries = [MemoryQuery.model_validate(q) for q in recipe.memory_queries]
    return MemoryHooks(
        queries=queries,
        writeback={
            "enabled": True,
            "write_events": [
                "task_started",
                "subtask_completed",
                "failure_detected",
                "recovery_success",
                "task_finished",
            ],
        },
    )


def _build_how_hooks(recipe: TaskRecipe) -> HowHooks:
    triggers = []
    for raw in recipe.how_triggers:
        trigger = SchemaHowTrigger(
            id=raw["id"],
            when=raw["when"],
            query_hint=raw["query_hint"],
            expected_strategy=raw.get("expected_strategy"),
        )
        triggers.append(trigger)
    return HowHooks(
        intervention_triggers=triggers,
        expected_strategies=["SAFETY", "CATALYST", "ABSTAIN"],
    )


def _build_auto_hooks(recipe: TaskRecipe) -> AutoHooks:
    candidates = [ExperimentCandidate.model_validate(e) for e in recipe.auto_experiments]
    return AutoHooks(
        experiment_candidates=candidates,
        prohibited_experiments=list(recipe.prohibited_experiments),
    )


def _build_cognitive_wiki(recipe: TaskRecipe) -> CognitiveWikiSync:
    cards = [WikiCardRef(id=wid, confidence=conf) for wid, conf in recipe.cognitive_wiki_cards]
    return CognitiveWikiSync(
        enabled=bool(recipe.cognitive_wiki_terms),
        query_terms=list(recipe.cognitive_wiki_terms),
        imported_cards=cards,
    )


def _build_evidence_trace(
    recipe: TaskRecipe,
    robot: str,
    scene_path: str | None,
    priors: list[EngineeringPrior],
    constraints: PhysicalConstraints,
) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    evidence.append(
        EvidenceItem(
            id=f"ev_{recipe.task_id}_intent",
            type="user_intent",
            claim=f"User requested {recipe.natural_language_goal}",
            source="user_prompt",
            confidence=1.0,
        )
    )
    evidence.append(
        EvidenceItem(
            id=f"ev_{recipe.task_id}_embodiment",
            type="embodiment_profile",
            claim=f"Robot is {robot} ({recipe.embodiment_type})",
            source=f"robot={robot}",
            confidence=0.9,
        )
    )
    if scene_path:
        evidence.append(
            EvidenceItem(
                id=f"ev_{recipe.task_id}_scene",
                type="scene_file",
                claim=f"Scene provided at {scene_path}",
                source=scene_path,
                confidence=0.85,
            )
        )

    for capability in recipe.required_capabilities:
        evidence.append(
            EvidenceItem(
                id=f"ev_{recipe.task_id}_cap_{capability}",
                type="schema_default",
                claim=f"Task requires capability {capability}",
                source="task_recipe",
                confidence=0.8,
            )
        )

    for prior in priors:
        evidence.append(
            EvidenceItem(
                id=f"ev_{recipe.task_id}_prior_{prior.id}",
                type="engineering_prior",
                claim=prior.description,
                source=prior.source,
                confidence=prior.confidence,
            )
        )

    for hc in constraints.hard_constraints:
        evidence.append(
            EvidenceItem(
                id=f"ev_{recipe.task_id}_constraint_{hc.id}",
                type="rosclaw_policy",
                claim=hc.description,
                source="physical_constraints",
                confidence=0.95,
            )
        )

    for query in recipe.memory_queries:
        evidence.append(
            EvidenceItem(
                id=f"ev_{recipe.task_id}_memory_{query['id']}",
                type="memory_retrieval",
                claim=f"Query memory for {query['intent']}: {query['query']}",
                source="rosclaw_memory",
                confidence=0.65,
            )
        )

    for wid, conf in recipe.cognitive_wiki_cards:
        evidence.append(
            EvidenceItem(
                id=f"ev_{recipe.task_id}_wiki_{wid}",
                type="cognitive_wiki",
                claim=f"Imported wiki card {wid}",
                source="cognitive_wiki",
                confidence=conf,
            )
        )

    return evidence


def _compute_quality(
    recipe: TaskRecipe,
    taxonomy: FailureTaxonomy,
    constraints: PhysicalConstraints,
    evidence: list[EvidenceItem],
) -> QualityScores:
    n_subtasks = len(recipe.subtasks)
    subtask_cov = 1.0 if n_subtasks >= 6 else n_subtasks / 6

    required_failure_count = max(2 * n_subtasks, 1)
    failure_cov = min(len(taxonomy.failures) / required_failure_count, 1.0)

    required_constraints = 6 if recipe.embodiment_type == "humanoid" else 2
    constraint_cov = min(len(constraints.hard_constraints + constraints.soft_constraints) / required_constraints, 1.0)

    key_fields = len(recipe.required_capabilities) + len(recipe.engineering_priors) + max(len(constraints.hard_constraints), 1) + len(recipe.subtasks)
    evidence_cov = min(len(evidence) / max(key_fields, 1), 1.0)

    compile_confidence = recipe.quality_base_confidence if hasattr(recipe, "quality_base_confidence") else 0.78
    compile_confidence = min(
        compile_confidence + 0.03 * subtask_cov + 0.02 * failure_cov + 0.02 * constraint_cov,
        0.95,
    )

    return QualityScores(
        schema_valid=True,
        subtask_coverage_score=round(subtask_cov, 2),
        failure_taxonomy_coverage_score=round(failure_cov, 2),
        constraint_coverage_score=round(constraint_cov, 2),
        evidence_coverage_score=round(evidence_cov, 2),
        compile_confidence=round(compile_confidence, 2),
    )


def _validate_recipe_robot(recipe: TaskRecipe, robot: str) -> None:
    if recipe.valid_robots is not None and robot not in recipe.valid_robots:
        raise TaskCardCompileError(
            f"Task '{recipe.task_id}' is not valid for robot '{robot}'. "
            f"Valid robots: {', '.join(recipe.valid_robots) or 'none'}."
        )


def compile_task(
    task_id: str,
    *,
    goal: str | None = None,
    robot: str = "unitree_g1",
    robot_id: str | None = None,
    body_path: str | None = None,
    embodiment_path: str | None = None,
    eurdf_path: str | None = None,
    scene_path: str | None = None,
    scene_id: str | None = None,
    enable_memory: bool = True,
    enable_cognitive_wiki: bool = True,
    strict: bool = False,
) -> TaskCard:
    """Compile a natural-language task into a TaskCard v1.

    Raises:
        TaskCardCompileError: if the task is invalid for the requested robot.
    """
    if task_id not in RECIPES:
        raise TaskCardCompileError(f"Unknown task_id: {task_id}")

    recipe = RECIPES[task_id]
    _validate_recipe_robot(recipe, robot)

    if not recipe.subtasks:
        raise TaskCardCompileError(
            f"Task '{task_id}' has no executable subtasks for robot '{robot}'."
        )

    metadata = TaskMetadata(
        task_id=task_id,
        title=recipe.title,
        created_at=_now_iso(),
        compiler_version="0.1.0",
        confidence=0.78,
        status="draft",
        tags=recipe.tags,
    )

    task = TaskGoal(
        natural_language_goal=goal or recipe.natural_language_goal,
        normalized_goal=recipe.normalized_goal,
        task_type=recipe.task_type,  # type: ignore[arg-type]
        task_family=recipe.task_family,
        domain=recipe.domain,
        difficulty=recipe.difficulty,  # type: ignore[arg-type]
        expected_outcome=recipe.expected_outcome,
        success_criteria=recipe.success_criteria,
    )

    embodiment = EmbodimentProfile(
        robot_id=robot_id or f"{robot}_01",
        robot_model=robot,
        embodiment_type=recipe.embodiment_type,
        body_profile_ref=embodiment_path,
        body_yaml_ref=body_path,
        eurdf_ref=eurdf_path,
        relevant_body_parts=recipe.relevant_body_parts,
        required_capabilities=recipe.required_capabilities,
        unavailable_capabilities=[],
        assumptions=recipe.assumptions,
    )

    scene = _resolve_scene(recipe, scene_path, scene_id)
    subtasks = _build_subtasks(recipe)
    taxonomy = _build_failure_taxonomy(recipe)
    constraints = _build_physical_constraints(recipe)
    priors = _build_priors(recipe)
    memory_hooks = _build_memory_hooks(recipe) if enable_memory else MemoryHooks()
    how_hooks = _build_how_hooks(recipe)
    auto_hooks = _build_auto_hooks(recipe)
    cognitive_wiki = _build_cognitive_wiki(recipe) if enable_cognitive_wiki else CognitiveWikiSync(enabled=False)
    evidence = _build_evidence_trace(recipe, robot, scene_path, priors, constraints)
    quality = _compute_quality(recipe, taxonomy, constraints, evidence)

    card = TaskCard(
        metadata=metadata,
        task=task,
        embodiment=embodiment,
        scene=scene,
        subtasks=subtasks,
        failure_taxonomy=taxonomy.model_dump(mode="json"),
        physical_constraints=constraints.model_dump(mode="json"),
        engineering_priors=priors,
        memory_hooks=memory_hooks,
        how_hooks=how_hooks,
        auto_hooks=auto_hooks,
        cognitive_wiki=cognitive_wiki,
        evidence_trace=evidence,
        quality=quality,
    )

    if strict:
        card.model_validate(card.model_dump(mode="json"))

    return card


class TaskCardCompiler:
    """Convenience wrapper around :func:`compile_task`."""

    def compile(
        self,
        task_id: str,
        **kwargs: Any,
    ) -> TaskCard:
        return compile_task(task_id, **kwargs)

    def compile_to_files(
        self,
        task_id: str,
        output_dir: Path,
        **kwargs: Any,
    ) -> dict[str, Path]:
        card = self.compile(task_id, **kwargs)
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        taskcard_path = output_dir / f"{task_id}.taskcard.yaml"
        evidence_path = output_dir / f"{task_id}.evidence.jsonl"
        report_path = output_dir / f"{task_id}.compile_report.md"

        taskcard_path.write_text(
            yaml.safe_dump(card.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

        with evidence_path.open("w", encoding="utf-8") as f:
            for ev in card.evidence_trace:
                f.write(json.dumps(ev.model_dump(mode="json"), ensure_ascii=False) + "\n")

        report_path.write_text(_render_report(card), encoding="utf-8")

        return {
            "taskcard": taskcard_path,
            "evidence": evidence_path,
            "report": report_path,
        }


def _render_report(card: TaskCard) -> str:
    lines = [
        f"# Compile Report: {card.metadata.task_id}",
        "",
        f"- **Status**: {card.metadata.status}",
        f"- **Confidence**: {card.metadata.confidence}",
        f"- **Robot**: {card.embodiment.robot_model}",
        f"- **Subtasks**: {len(card.subtasks)}",
        f"- **Failures**: {len(card.failure_taxonomy.get('failures', []))}",
        f"- **Hard constraints**: {len(card.physical_constraints.get('hard_constraints', []))}",
        f"- **Evidence items**: {len(card.evidence_trace)}",
        "",
        "## Subtasks",
        "",
    ]
    for st in card.subtasks:
        lines.append(f"- {st.id}: {st.name}")
    lines.extend(["", "## Quality", ""])
    q = card.quality
    lines.append(f"- schema_valid: {q.schema_valid}")
    lines.append(f"- subtask_coverage_score: {q.subtask_coverage_score}")
    lines.append(f"- failure_taxonomy_coverage_score: {q.failure_taxonomy_coverage_score}")
    lines.append(f"- constraint_coverage_score: {q.constraint_coverage_score}")
    lines.append(f"- evidence_coverage_score: {q.evidence_coverage_score}")
    lines.append(f"- compile_confidence: {q.compile_confidence}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "TaskCardCompiler",
    "compile_task",
    "TaskCardCompileError",
]
