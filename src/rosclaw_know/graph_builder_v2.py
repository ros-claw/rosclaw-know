"""Sprint 5: typed Physical Knowledge Graph V2.

Builds a :class:`networkx.MultiDiGraph` from the v2 typed objects produced
by Sprints 1–4.  Every node carries:

* ``node_type``: one of ``Domain | FailureMode | FixPattern |
  ConstraintPattern | TaskCard | EmbodimentCard | VerifierCard |
  EvidenceTrace``
* ``payload``: the original Pydantic object (or, for Domain pseudo-nodes,
  the canonical domain string)
* ``domain`` (when applicable): the canonical domain bucket.

Every edge carries:

* ``relation``: one of the 12 :data:`EdgeRelation` literals (plan §6.2).
* ``weight``: float, default 1.0 — used by the hybrid retriever.

Plan §11.5 acceptance gates (enforced by :func:`assert_acceptance`):

* every ``FixPattern`` connects to ≥1 ``FailureMode`` (FIXES edge);
* every ``TaskCard`` connects to ≥1 ``Domain`` (APPLIES_TO) AND ≥1
  ``VerifierCard`` (VALIDATED_BY);
* every ``EvidenceTrace`` is linked to a pattern_id (DERIVED_FROM /
  IMPROVED_BY / REGRESSED_BY depending on uplift).

The graph is intentionally a *multi*-digraph: the same pair of nodes can be
linked by multiple distinct relations (e.g. FixPattern -FIXES→ FailureMode
*and* FixPattern -DERIVED_FROM→ FailureMode for the curated baseline).
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import networkx as nx

from .prompts import FRONTIER_DOMAINS
from .schemas import (
    ConstraintPattern,
    EmbodimentCard,
    EvidenceTrace,
    FailureMode,
    FixPattern,
    TaskCard,
    VerifierCard,
)

log = logging.getLogger("rosclaw_know.graph_builder_v2")

# ── Edge relation enumeration (plan §6.2) ────────────────────────────────

EdgeRelation = Literal[
    "CAUSES",
    "FIXES",
    "VIOLATES",
    "CONSTRAINED_BY",
    "OBSERVED_IN",
    "APPLIES_TO",
    "CONTRAINDICATED_FOR",
    "VALIDATED_BY",
    "TRANSFERABLE_TO",
    "DERIVED_FROM",
    "IMPROVED_BY",
    "REGRESSED_BY",
]

ALL_RELATIONS: tuple[EdgeRelation, ...] = (
    "CAUSES",
    "FIXES",
    "VIOLATES",
    "CONSTRAINED_BY",
    "OBSERVED_IN",
    "APPLIES_TO",
    "CONTRAINDICATED_FOR",
    "VALIDATED_BY",
    "TRANSFERABLE_TO",
    "DERIVED_FROM",
    "IMPROVED_BY",
    "REGRESSED_BY",
)

# Sentinel node-id prefix for the domain pseudo-nodes.  Picking a prefix
# that cannot collide with any real id pattern (which is enforced by
# ``schemas.FailureMode.id = "^failure_…"``, etc.).
_DOMAIN_NODE_PREFIX = "domain::"


# ── Public dataclass: a lightweight inventory of what was wired ──────────


@dataclass(frozen=True)
class GraphBuildReport:
    """Connectivity summary returned by :func:`build_physical_graph`."""

    node_count: int
    edge_count: int
    nodes_by_type: dict[str, int]
    edges_by_relation: dict[str, int]
    violations: list[str]
    """Plan §11.5 acceptance violations.  Empty list = clean build."""


# ── helpers ──────────────────────────────────────────────────────────────


def _domain_node_id(domain: str) -> str:
    """Return the canonical pseudo-node id for a domain bucket."""
    return f"{_DOMAIN_NODE_PREFIX}{domain}"


def _add_node(g: nx.MultiDiGraph, node_id: str, **attrs: Any) -> None:
    """Idempotently add or merge attributes on a node."""
    if node_id in g:
        g.nodes[node_id].update(attrs)
    else:
        g.add_node(node_id, **attrs)


def _add_edge(
    g: nx.MultiDiGraph,
    src: str,
    dst: str,
    *,
    relation: EdgeRelation,
    weight: float = 1.0,
    **attrs: Any,
) -> None:
    """Add a typed edge (no dedup — a MultiDiGraph allows parallel edges)."""
    g.add_edge(src, dst, relation=relation, weight=weight, **attrs)


# ── node wiring ──────────────────────────────────────────────────────────


def _add_domain_nodes(g: nx.MultiDiGraph) -> None:
    """Seed one pseudo-node per :data:`FRONTIER_DOMAINS` member.

    Plan §11.5 wants every TaskCard to "connect to a domain".  We model
    domains as first-class graph nodes so the connectivity claim is
    structural, not just an attribute lookup.
    """
    for d in FRONTIER_DOMAINS:
        _add_node(
            g,
            _domain_node_id(d),
            node_type="Domain",
            domain=d,
            payload=d,
        )


def _add_failure_modes(
    g: nx.MultiDiGraph, failures: Iterable[FailureMode]
) -> None:
    for fm in failures:
        _add_node(
            g,
            fm.id,
            node_type="FailureMode",
            domain=fm.domain,
            payload=fm,
        )


def _add_fix_patterns(
    g: nx.MultiDiGraph, fixes: Iterable[FixPattern]
) -> None:
    for fp in fixes:
        _add_node(
            g,
            fp.id,
            node_type="FixPattern",
            domain=fp.domain,
            payload=fp,
        )


def _add_constraints(
    g: nx.MultiDiGraph, constraints: Iterable[ConstraintPattern]
) -> None:
    for c in constraints:
        _add_node(g, c.id, node_type="ConstraintPattern", payload=c)


def _add_tasks(
    g: nx.MultiDiGraph, tasks: Iterable[TaskCard]
) -> None:
    for t in tasks:
        _add_node(
            g,
            t.id,
            node_type="TaskCard",
            domain=t.domain,
            payload=t,
        )


def _add_embodiments(
    g: nx.MultiDiGraph, embodiments: Iterable[EmbodimentCard]
) -> None:
    for e in embodiments:
        _add_node(
            g, e.id, node_type="EmbodimentCard", payload=e,
        )


def _add_verifiers(
    g: nx.MultiDiGraph, verifiers: Iterable[VerifierCard]
) -> None:
    for v in verifiers:
        _add_node(g, v.id, node_type="VerifierCard", payload=v)


def _add_evidence_traces(
    g: nx.MultiDiGraph, traces: Iterable[EvidenceTrace]
) -> None:
    for ev in traces:
        _add_node(
            g, ev.trace_id, node_type="EvidenceTrace", payload=ev,
        )


# ── edge wiring ──────────────────────────────────────────────────────────


def _wire_fix_to_failure(
    g: nx.MultiDiGraph, fixes: Iterable[FixPattern]
) -> None:
    """FixPattern -FIXES→ FailureMode (plan §6.2 example)."""
    for fp in fixes:
        for fid in fp.failure_ids:
            if fid not in g:
                log.warning(
                    "fix %s references unknown failure %s — wiring anyway",
                    fp.id, fid,
                )
                # Keep the edge — it's better to surface a dangling node
                # than to silently drop the link.  The validator will
                # flag the missing FailureMode separately.
                _add_node(g, fid, node_type="FailureMode_missing")
            _add_edge(g, fp.id, fid, relation="FIXES")


def _wire_failure_to_task(
    g: nx.MultiDiGraph,
    failures: Sequence[FailureMode],
    tasks: Iterable[TaskCard],
) -> None:
    """FailureMode -OBSERVED_IN→ TaskCard.

    Driven by ``TaskCard.common_failure_modes`` (the canonical place that
    lists which failures show up in which tasks).
    """
    known_failures = {f.id for f in failures}
    for t in tasks:
        for fid in t.common_failure_modes:
            if fid not in known_failures:
                # TaskCards from Sprint 2 sometimes reference failures
                # that didn't make it into the Sprint-1 taxonomy.  We
                # tolerate this — the bridge_index will still flag the
                # mismatch in a downstream lint.
                continue
            _add_edge(g, fid, t.id, relation="OBSERVED_IN")


def _wire_task_to_domain(
    g: nx.MultiDiGraph, tasks: Iterable[TaskCard]
) -> None:
    """TaskCard -APPLIES_TO→ Domain (pseudo-node).

    Plan §11.5: "每个 TaskCard 至少连接一个 domain".  The Domain pseudo-node is
    seeded in :func:`_add_domain_nodes`.
    """
    for t in tasks:
        _add_edge(g, t.id, _domain_node_id(t.domain), relation="APPLIES_TO")


def _wire_fix_to_domain(
    g: nx.MultiDiGraph, fixes: Iterable[FixPattern]
) -> None:
    """FixPattern -APPLIES_TO→ Domain.

    Lets the hybrid retriever do same-domain boosting in one hop.
    """
    for fp in fixes:
        _add_edge(g, fp.id, _domain_node_id(fp.domain), relation="APPLIES_TO")


def _wire_task_to_verifier(
    g: nx.MultiDiGraph,
    tasks: Iterable[TaskCard],
    verifiers: Sequence[VerifierCard],
) -> None:
    """TaskCard -VALIDATED_BY→ VerifierCard.

    Match by ``verifier_type``.  When more than one VerifierCard advertises
    the same type, every match gets wired (multi-edge OK).
    """
    by_type: dict[str, list[VerifierCard]] = {}
    for v in verifiers:
        by_type.setdefault(v.verifier_type, []).append(v)

    for t in tasks:
        matches = by_type.get(t.verifier_type, [])
        for v in matches:
            _add_edge(g, t.id, v.id, relation="VALIDATED_BY")


def _wire_fix_to_embodiment(
    g: nx.MultiDiGraph,
    fixes: Iterable[FixPattern],
    embodiments: Sequence[EmbodimentCard],
    *,
    task_family_to_embodiment: dict[str, list[str]] | None = None,
) -> None:
    """FixPattern -APPLIES_TO→ EmbodimentCard.

    Wire driven by an optional ``task_family → embodiment_id`` map.  When
    a FixPattern's source task_family resolves to an embodiment id we know
    about, an APPLIES_TO edge is added.  Missing maps are silently
    skipped — having no embodiment hint is normal for cross-cutting
    optimizer patterns like ``vectorize_inner_loop``.
    """
    if not task_family_to_embodiment:
        return
    known = {e.id for e in embodiments}
    for fp in fixes:
        for family in _extract_task_families(fp):
            for eid in task_family_to_embodiment.get(family, []):
                if eid not in known:
                    continue
                _add_edge(g, fp.id, eid, relation="APPLIES_TO")


def _wire_fix_contraindications(
    g: nx.MultiDiGraph, fixes: Iterable[FixPattern]
) -> None:
    """FixPattern -CONTRAINDICATED_FOR→ TaskCard or EmbodimentCard.

    Pulls the entries from :attr:`FixPattern.anti_patterns` looking for
    references to known nodes.  Free-form prose entries that don't match
    any node id are skipped.
    """
    for fp in fixes:
        for entry in fp.anti_patterns:
            for token in _tokenize_reference(entry):
                if token in g:
                    _add_edge(g, fp.id, token, relation="CONTRAINDICATED_FOR")


def _wire_failure_transferable(
    g: nx.MultiDiGraph,
    failures: Sequence[FailureMode],
    tasks: Sequence[TaskCard],
) -> None:
    """FailureMode -TRANSFERABLE_TO→ TaskCard.

    Sister tasks of the same family in the same domain where the failure
    *isn't* listed as common but plausibly applies (same family means the
    artefact type and verifier are aligned).  Useful for "what could go
    wrong I haven't been told about" queries.
    """
    # group tasks by family
    by_family: dict[str, list[TaskCard]] = {}
    for t in tasks:
        by_family.setdefault(t.task_family, []).append(t)

    for fm in failures:
        for t in tasks:
            if fm.domain != t.domain:
                continue
            if fm.id in t.common_failure_modes:
                continue
            # any sister task in this family already lists fm?
            sisters = by_family.get(t.task_family, [])
            if any(fm.id in s.common_failure_modes for s in sisters if s.id != t.id):
                _add_edge(g, fm.id, t.id, relation="TRANSFERABLE_TO")


def _wire_evidence_traces(
    g: nx.MultiDiGraph, traces: Iterable[EvidenceTrace]
) -> None:
    """EvidenceTrace -DERIVED_FROM | IMPROVED_BY | REGRESSED_BY → pattern.

    Plan §11.5: "每条 EvidenceTrace 必须连接 pattern_id".  Choice of
    relation depends on the recorded uplift:
      * ``best_delta_5 > 0`` → IMPROVED_BY
      * ``best_delta_5 < 0`` → REGRESSED_BY
      * unknown / zero → DERIVED_FROM (neutral evidence)
    """
    for ev in traces:
        if not ev.pattern_id:
            log.warning(
                "evidence trace %s has no pattern_id — cannot wire",
                ev.trace_id,
            )
            continue
        if ev.pattern_id not in g:
            _add_node(g, ev.pattern_id, node_type="FixPattern_external")
        delta = ev.best_delta_5
        if delta is None or delta == 0:
            rel: EdgeRelation = "DERIVED_FROM"
        elif delta > 0:
            rel = "IMPROVED_BY"
        else:
            rel = "REGRESSED_BY"
        _add_edge(g, ev.trace_id, ev.pattern_id, relation=rel)


def _wire_fix_to_constraint(
    g: nx.MultiDiGraph,
    fixes: Iterable[FixPattern],
    constraints: Iterable[ConstraintPattern],
) -> None:
    """FixPattern -VIOLATES→ ConstraintPattern.

    Detection heuristic: a fix violates a constraint if any of the fix's
    anti_patterns names the constraint id.  Cheap but precise — won't
    fire by accident.
    """
    known_constraints = {c.id for c in constraints}
    if not known_constraints:
        return
    for fp in fixes:
        for ap in fp.anti_patterns:
            for cid in known_constraints:
                if cid in ap:
                    _add_edge(g, fp.id, cid, relation="VIOLATES")


def _wire_task_constrained_by(
    g: nx.MultiDiGraph,
    tasks: Iterable[TaskCard],
    constraints: Iterable[ConstraintPattern],
) -> None:
    """TaskCard -CONSTRAINED_BY→ ConstraintPattern.

    Detection: any hard_constraint string mentioning the constraint id.
    """
    known_constraints = {c.id for c in constraints}
    if not known_constraints:
        return
    for t in tasks:
        for hc in t.hard_constraints:
            for cid in known_constraints:
                if cid in hc:
                    _add_edge(g, t.id, cid, relation="CONSTRAINED_BY")


# ── utility ──────────────────────────────────────────────────────────────


def _tokenize_reference(s: str) -> list[str]:
    """Pick out potential node ids from a free-form string.

    The Sprint-1 taxonomy uses ``failure_<id>`` and ``compiled_<id>`` /
    ``task_<id>`` prefixes; we grep for tokens that look like those.
    """
    import re
    return re.findall(r"\b(?:failure|fix|compiled|task|embodiment|verifier|trace)_[a-z0-9_]+\b", s.lower())


def _extract_task_families(fp: FixPattern) -> list[str]:
    """Best-effort family extraction from a FixPattern.

    ``FixPattern`` doesn't carry task_families directly (that's a
    PatternCardV2 field), so we sniff the implementation_steps /
    expected_verifier_signals strings.  If neither yields anything we
    return ``[]`` and the caller falls back to APPLIES_TO via domain.
    """
    haystack = " ".join(
        fp.implementation_steps + fp.expected_verifier_signals
    ).lower()
    families: list[str] = []
    for hint in (
        "pid_tuning", "quadrotor", "manipulator", "kernel", "cuda",
        "wireless_channel", "particle_physics", "optics", "robotics",
    ):
        if hint in haystack:
            families.append(hint)
    return families


# ── main entry ───────────────────────────────────────────────────────────


def build_physical_graph(
    failures: Sequence[FailureMode] = (),
    fixes: Sequence[FixPattern] = (),
    tasks: Sequence[TaskCard] = (),
    embodiments: Sequence[EmbodimentCard] = (),
    verifiers: Sequence[VerifierCard] = (),
    traces: Sequence[EvidenceTrace] = (),
    *,
    constraints: Sequence[ConstraintPattern] = (),
    task_family_to_embodiment: dict[str, list[str]] | None = None,
) -> tuple[nx.MultiDiGraph, GraphBuildReport]:
    """Plan §11.5 entry point: build the typed physical knowledge graph.

    Returns the graph plus a :class:`GraphBuildReport` enumerating which
    plan acceptance gates passed.  Callers can inspect
    ``report.violations`` to decide whether to error out.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()

    # ── nodes ──
    _add_domain_nodes(g)
    _add_failure_modes(g, failures)
    _add_fix_patterns(g, fixes)
    _add_constraints(g, constraints)
    _add_tasks(g, tasks)
    _add_embodiments(g, embodiments)
    _add_verifiers(g, verifiers)
    _add_evidence_traces(g, traces)

    # ── edges ──
    _wire_fix_to_failure(g, fixes)
    _wire_failure_to_task(g, list(failures), tasks)
    _wire_task_to_domain(g, tasks)
    _wire_fix_to_domain(g, fixes)
    _wire_task_to_verifier(g, tasks, list(verifiers))
    _wire_fix_to_embodiment(
        g, fixes, list(embodiments),
        task_family_to_embodiment=task_family_to_embodiment,
    )
    _wire_fix_contraindications(g, fixes)
    _wire_failure_transferable(g, list(failures), list(tasks))
    _wire_evidence_traces(g, traces)
    _wire_fix_to_constraint(g, fixes, constraints)
    _wire_task_constrained_by(g, tasks, constraints)

    # ── report ──
    nodes_by_type: dict[str, int] = {}
    for _n, attrs in g.nodes(data=True):
        nodes_by_type[attrs.get("node_type", "unknown")] = (
            nodes_by_type.get(attrs.get("node_type", "unknown"), 0) + 1
        )
    edges_by_relation: dict[str, int] = {}
    for _u, _v, attrs in g.edges(data=True):
        edges_by_relation[attrs.get("relation", "unknown")] = (
            edges_by_relation.get(attrs.get("relation", "unknown"), 0) + 1
        )

    violations = _check_acceptance(g, fixes, tasks, traces)

    report = GraphBuildReport(
        node_count=g.number_of_nodes(),
        edge_count=g.number_of_edges(),
        nodes_by_type=dict(sorted(nodes_by_type.items())),
        edges_by_relation=dict(sorted(edges_by_relation.items())),
        violations=violations,
    )
    log.info(
        "Built physical graph: %d nodes, %d edges, %d violations",
        report.node_count, report.edge_count, len(violations),
    )
    return g, report


def _check_acceptance(
    g: nx.MultiDiGraph,
    fixes: Sequence[FixPattern],
    tasks: Sequence[TaskCard],
    traces: Sequence[EvidenceTrace],
) -> list[str]:
    """Run plan §11.5 acceptance and return list of violation messages."""
    out: list[str] = []

    # every FixPattern → ≥1 FailureMode via FIXES
    for fp in fixes:
        ok = any(
            data.get("relation") == "FIXES"
            and g.nodes.get(v, {}).get("node_type", "").startswith("FailureMode")
            for _u, v, data in g.out_edges(fp.id, data=True)
        )
        if not ok:
            out.append(
                f"FixPattern {fp.id!r} has no FIXES edge to any FailureMode"
            )

    # every TaskCard → ≥1 Domain via APPLIES_TO AND ≥1 VerifierCard via VALIDATED_BY
    for t in tasks:
        has_domain = any(
            data.get("relation") == "APPLIES_TO"
            and g.nodes.get(v, {}).get("node_type") == "Domain"
            for _u, v, data in g.out_edges(t.id, data=True)
        )
        if not has_domain:
            out.append(f"TaskCard {t.id!r} not linked to any Domain")
        has_verifier = any(
            data.get("relation") == "VALIDATED_BY"
            for _u, _v, data in g.out_edges(t.id, data=True)
        )
        if not has_verifier:
            out.append(
                f"TaskCard {t.id!r} not linked to any VerifierCard"
            )

    # every EvidenceTrace → pattern via DERIVED_FROM/IMPROVED_BY/REGRESSED_BY
    for ev in traces:
        ok = any(
            data.get("relation") in ("DERIVED_FROM", "IMPROVED_BY", "REGRESSED_BY")
            for _u, _v, data in g.out_edges(ev.trace_id, data=True)
        )
        if not ok:
            out.append(
                f"EvidenceTrace {ev.trace_id!r} is not linked to any pattern"
            )
    return out


__all__ = [
    "ALL_RELATIONS",
    "EdgeRelation",
    "GraphBuildReport",
    "build_physical_graph",
]
