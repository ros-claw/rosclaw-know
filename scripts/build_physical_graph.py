#!/usr/bin/env python3
"""Sprint 5: build the typed Physical Knowledge Graph V2 (plan §11.5).

Loads every v2 typed-object YAML asset produced by Sprints 1-4 plus the
Sprint-5 seed cards, and emits ``data/assets/physical_graph.json`` (node-
link format) plus a ``pattern_cards_v2.yaml`` manifest for downstream
consumers (hybrid retriever, Sprint 7 task-pack builder).

Inputs::

    data/assets/failure_taxonomy.yaml      (Sprint 1)
    data/assets/task_cards.yaml             (Sprint 2)
    data/assets/trajectory_patterns.yaml    (Sprint 3, optional)
    data/assets/embodiments.yaml            (Sprint 5)
    data/assets/verifier_cards.yaml         (Sprint 5)
    data/assets/evidence_traces.jsonl       (Sprint 6, optional)

Outputs::

    data/assets/physical_graph.json         (node-link encoding)
    data/assets/pattern_cards_v2.yaml       (compiled PatternCardV2s)

Exits non-zero when plan §11.5 acceptance is violated; pass
``--allow-violations`` to override.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterable
from pathlib import Path

import networkx as nx
import yaml

from rosclaw_know import config
from rosclaw_know.graph_builder_v2 import build_physical_graph
from rosclaw_know.pattern_compiler_v2 import (
    CompileContext,
    compile_pattern_card,
)
from rosclaw_know.schemas import (
    CandidatePattern,
    EmbodimentCard,
    EvidenceTrace,
    FailureMode,
    FixPattern,
    PatternCardV2,
    TaskCard,
    VerifierCard,
)

logger = logging.getLogger("build_physical_graph")


# ── loaders ──────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        logger.warning("missing %s — treating as empty", path)
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_failures(path: Path) -> list[FailureMode]:
    raw = _load_yaml(path).get("failures", [])
    return [FailureMode.model_validate(f) for f in raw]


def _load_tasks(path: Path) -> list[TaskCard]:
    raw = _load_yaml(path).get("task_cards", [])
    return [TaskCard.model_validate(t) for t in raw]


def _load_candidates(path: Path) -> list[CandidatePattern]:
    raw = _load_yaml(path).get("candidate_patterns", [])
    return [CandidatePattern.model_validate(c) for c in raw]


def _load_embodiments(path: Path) -> list[EmbodimentCard]:
    raw = _load_yaml(path).get("embodiments", [])
    return [EmbodimentCard.model_validate(e) for e in raw]


def _load_verifiers(path: Path) -> list[VerifierCard]:
    raw = _load_yaml(path).get("verifiers", [])
    return [VerifierCard.model_validate(v) for v in raw]


def _load_evidence_traces(path: Path) -> list[EvidenceTrace]:
    """JSONL loader — empty when file doesn't exist."""
    if not path.is_file():
        return []
    traces: list[EvidenceTrace] = []
    with path.open(encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            traces.append(EvidenceTrace.model_validate(json.loads(ln)))
    return traces


# ── adapters ─────────────────────────────────────────────────────────────


def _pattern_card_to_fix(card: PatternCardV2, *, failure_id: str | None) -> FixPattern:
    """Render a PatternCardV2 as a FixPattern for the graph layer.

    ``PatternCardV2`` is the agent-facing object; ``FixPattern`` is the
    graph node.  Most fields map directly; the few that don't lose
    nothing the graph cares about (the markdown body is not used in the
    graph).
    """
    return FixPattern(
        id=card.id,
        failure_ids=[failure_id] if failure_id else [],
        domain=card.domain,
        fix_summary=card.symptom,
        preconditions=card.preconditions,
        implementation_steps=[card.next_experiment, card.patch_sketch],
        code_targets=[card.code_target] if card.code_target else [],
        expected_verifier_signals=card.expected_verifier_signals,
        anti_patterns=card.anti_patterns + card.contraindications,
        source_ids=card.source_ids,
    )


# Cross-cutting Sprint-3 candidates are tagged with their source task
# family (often ``unknown_optimization``) which under-sells how broadly
# they apply.  Sprint 7 needs the hybrid retriever to recognise these
# as applicable across multiple families so e.g. a CUDA-flavoured query
# can recall ``compiled_vectorize_inner_loop``.  This map widens
# ``task_families`` post-compile.
_CROSS_FAMILY_ALIASES: dict[str, list[str]] = {
    "compiled_vectorize_inner_loop": [
        "kernel_engineering_optimization",
        "cryptographic_optimization",
        "computer_systems_optimization",
        "sustainable_data_center_control_optimization",
        "wireless_channel_simulation_optimization",
        "robotics_optimization",
        "particle_physics_optimization",
    ],
    "compiled_add_boundary_validation": [
        "robotics_optimization",
        "kernel_engineering_optimization",
        "cryptographic_optimization",
        "computer_systems_optimization",
        "additive_manufacturing_optimization",
        "structural_optimization_optimization",
    ],
    "compiled_warm_start_from_prior_best": [
        "robotics_optimization",
        "kernel_engineering_optimization",
        "additive_manufacturing_optimization",
        "aerodynamics_optimization",
        "molecular_mechanics_optimization",
        "astrodynamics_optimization",
        "py_portfolio_opt_optimization",
        "inventory_optimization_optimization",
    ],
    "compiled_generic_time_budget": [
        "robotics_optimization",
        "kernel_engineering_optimization",
        "computer_systems_optimization",
        "optics_optimization",
        "aerodynamics_optimization",
        "particle_physics_optimization",
    ],
    "compiled_swap_random_search_to_structured_optimizer": [
        "robotics_optimization",
        "kernel_engineering_optimization",
        "molecular_mechanics_optimization",
        "aerodynamics_optimization",
        "additive_manufacturing_optimization",
        "reaction_optimisation_optimization",
        "py_portfolio_opt_optimization",
        "wireless_channel_simulation_optimization",
    ],
    "compiled_add_time_budget": [
        "robotics_optimization",
        "kernel_engineering_optimization",
        "computer_systems_optimization",
        "sustainable_data_center_control_optimization",
        "job_shop_optimization",
    ],
}


def _widen_task_families(card: PatternCardV2) -> PatternCardV2:
    """Add cross-cutting task_family aliases to a compiled pattern."""
    extras = _CROSS_FAMILY_ALIASES.get(card.id)
    if not extras:
        return card
    merged: list[str] = list(card.task_families)
    for fam in extras:
        if fam not in merged:
            merged.append(fam)
    return card.model_copy(update={"task_families": merged})


def compile_candidates(
    candidates: Iterable[CandidatePattern],
    failures: Iterable[FailureMode],
) -> tuple[list[PatternCardV2], list[FixPattern]]:
    """Sprint-4 compile path: candidate → PatternCardV2 → FixPattern.

    Patterns that the Sprint-3 miner couldn't tag with an explicit
    ``failure_id`` (the cross-cutting optimiser patterns like
    ``vectorize_inner_loop``) get a fallback failure_id from
    :data:`_MUTATION_KIND_TO_FAILURE`.  This is honest — those patterns
    really do address a generic engineering failure — and it lets the
    Sprint 5 acceptance gate "every FixPattern → ≥1 FailureMode" pass
    without contorting the schema.
    """
    fm_lookup = {f.id: f for f in failures}
    ctx = CompileContext(failure_modes=fm_lookup)
    cards: list[PatternCardV2] = []
    fixes: list[FixPattern] = []
    for c in candidates:
        card = compile_pattern_card(c, context=ctx)
        card = _widen_task_families(card)
        cards.append(card)
        fid = _resolve_failure_id(c, fm_lookup)
        fixes.append(_pattern_card_to_fix(card, failure_id=fid))
    return cards, fixes


# Fallback mapping: when a candidate has no explicit failure_id, its
# dominant mutation kind picks one from the generic failures introduced
# in Sprint 5.  Order matters — first match wins.
_MUTATION_KIND_TO_FAILURE: tuple[tuple[str, str], ...] = (
    ("add_input_validation",    "failure_generic_unvalidated_input"),
    ("add_time_budget",         "failure_generic_runaway_search"),
    ("swap_optimizer",          "failure_generic_random_search_inefficiency"),
    ("vectorize_loop",          "failure_generic_python_loop_overhead"),
    ("add_initialization_seed", "failure_generic_cold_start_search"),
    ("add_output_clamp",        "failure_actuator_clamp_missing"),
    ("set_parameter_zero",      "failure_pid_integrator_windup"),
)


def _resolve_failure_id(
    candidate: CandidatePattern,
    fm_lookup: dict[str, FailureMode],
) -> str | None:
    """Return the candidate's failure_id, falling back to mutation kind."""
    if candidate.failure_id and candidate.failure_id in fm_lookup:
        return candidate.failure_id
    seen_kinds = {m.kind for m in candidate.successful_mutations}
    for kind, fid in _MUTATION_KIND_TO_FAILURE:
        if kind in seen_kinds and fid in fm_lookup:
            return fid
    return None


# ── task_family → embodiment_id map (Sprint 5 wiring hint) ──────────────


_FAMILY_TO_EMBODIMENT: dict[str, list[str]] = {
    "robotics_optimization": ["embodiment_quadrotor", "embodiment_manipulator"],
    "molecular_mechanics_optimization": [],  # no clear embodiment
    "kernel_engineering_optimization": ["embodiment_gpu_kernel"],
    "communication_engineering_optimization": [],
    "cryptographic_optimization": ["embodiment_gpu_kernel"],
    "sustainable_data_center_control_optimization": ["embodiment_data_center"],
    "wireless_channel_simulation_optimization": [],
    "particle_physics_optimization": [],
    "optics_optimization": ["embodiment_optical_system"],
    "structural_optimization_optimization": [],
    "aerodynamics_optimization": [],
    "additive_manufacturing_optimization": ["embodiment_manipulator"],
    "energy_storage_optimization": [],
    "power_systems_optimization": [],
    "astrodynamics_optimization": [],
    "computer_systems_optimization": ["embodiment_data_center"],
    "single_cell_analysis_optimization": [],
    "reaction_optimisation_optimization": [],
    "inventory_optimization_optimization": [],
    "job_shop_optimization": [],
    "py_portfolio_opt_optimization": [],
    "unknown_optimization": [],
}


# ── writers ──────────────────────────────────────────────────────────────


def _dump_graph(g: nx.MultiDiGraph, out_path: Path) -> None:
    """Serialise the MultiDiGraph as node-link JSON.

    networkx ``node_link_data`` can't pickle our Pydantic payloads, so we
    strip them down to a shallow dict (id, type, domain).  Full payload
    re-hydration is the caller's job — they should re-load the YAML
    manifests directly.  Use ``edges="edges"`` so the loader stays on
    the modern key name (silences the FutureWarning for ``links``).
    """
    data = nx.node_link_data(g, edges="edges")
    # Trim node payloads to JSON-serialisable shapes
    for node in data["nodes"]:
        payload = node.get("payload")
        if payload is None:
            continue
        if hasattr(payload, "model_dump"):
            node["payload"] = payload.model_dump(mode="json")
        else:
            # Domain pseudo-node payloads are bare strings.
            node["payload"] = str(payload)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(out_path)


def _dump_pattern_manifest(cards: list[PatternCardV2], out_path: Path) -> None:
    """YAML manifest of PatternCardV2s for the hybrid retriever."""
    payload = {
        "schema_version": "2.0",
        "pattern_cards": [c.model_dump(mode="json") for c in cards],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    tmp.replace(out_path)


# ── CLI ──────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build the typed Physical Knowledge Graph V2.",
    )
    assets = config.ASSETS_DIR
    p.add_argument(
        "--failures", default=str(assets / "failure_taxonomy.yaml"),
    )
    p.add_argument("--tasks", default=str(assets / "task_cards.yaml"))
    p.add_argument("--candidates", default=str(assets / "trajectory_patterns.yaml"))
    p.add_argument("--embodiments", default=str(assets / "embodiments.yaml"))
    p.add_argument("--verifiers", default=str(assets / "verifier_cards.yaml"))
    p.add_argument(
        "--evidence", default=str(assets / "evidence_traces.jsonl"),
        help="Optional EvidenceTrace JSONL (Sprint 6).",
    )
    p.add_argument(
        "--out-graph", default=str(assets / "physical_graph.json"),
    )
    p.add_argument(
        "--out-pattern-manifest",
        default=str(assets / "pattern_cards_v2.yaml"),
    )
    p.add_argument(
        "--allow-violations", action="store_true",
        help="Exit 0 even when plan §11.5 acceptance is violated.",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    failures = _load_failures(Path(args.failures))
    tasks = _load_tasks(Path(args.tasks))
    candidates = _load_candidates(Path(args.candidates))
    embodiments = _load_embodiments(Path(args.embodiments))
    verifiers = _load_verifiers(Path(args.verifiers))
    traces = _load_evidence_traces(Path(args.evidence))

    print(
        f"Loaded: {len(failures)} failures, {len(tasks)} tasks, "
        f"{len(candidates)} candidates, {len(embodiments)} embodiments, "
        f"{len(verifiers)} verifiers, {len(traces)} evidence traces"
    )

    cards, fixes = compile_candidates(candidates, failures)

    g, report = build_physical_graph(
        failures=failures,
        fixes=fixes,
        tasks=tasks,
        embodiments=embodiments,
        verifiers=verifiers,
        traces=traces,
        task_family_to_embodiment=_FAMILY_TO_EMBODIMENT,
    )

    print()
    print(f"Graph: {report.node_count} nodes, {report.edge_count} edges")
    print("  Nodes by type:")
    for k, v in sorted(report.nodes_by_type.items()):
        print(f"    {k:24s} {v}")
    print("  Edges by relation:")
    for k, v in sorted(report.edges_by_relation.items()):
        print(f"    {k:24s} {v}")

    if report.violations:
        print(f"\nViolations ({len(report.violations)}):", file=sys.stderr)
        for v in report.violations[:20]:
            print(f"  - {v}", file=sys.stderr)
        if len(report.violations) > 20:
            print(
                f"  …and {len(report.violations) - 20} more",
                file=sys.stderr,
            )
        if not args.allow_violations:
            return 1

    _dump_graph(g, Path(args.out_graph))
    _dump_pattern_manifest(cards, Path(args.out_pattern_manifest))

    print(f"\nWrote graph to {args.out_graph}")
    print(f"Wrote pattern manifest to {args.out_pattern_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
