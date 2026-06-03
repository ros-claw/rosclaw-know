#!/usr/bin/env python3
"""Sprint 13 — widen the cross-embodiment surface from 3/8 → 8/8 event_types.

After Sprint 10 made the transfer table auto-derived (no hand-curated
``event_type → pattern_id`` rows), the bottleneck shifted to *catalog
coverage*: only three of the eight canonical RobotEvent event_types had
any matching FixPattern in the compiled graph.

Sprint 13 closes that gap by adding to the catalog:

  * 4 new :class:`FailureMode` entries (collision, joint_limit_violation,
    sensor_outlier, safety_stop) — pin the event_type vocabulary in the
    structural-identity fields that
    :func:`cross_embodiment._haystack_for` reads.
  * 7 new :class:`FixPattern` nodes — wire each new failure (plus three
    pre-existing orphans: ``failure_planning_divergence``,
    ``failure_kv_cache_unbounded_growth``, ``failure_gradient_explosion``)
    into the FIXES sub-graph so the auto-derived transfer table picks
    them up.

The script is **idempotent** — re-running it after a successful pass
leaves the assets untouched.  It writes the acceptance report at
``data/assets/sprint13_acceptance_report.{json,md}``.

Usage::

    .venv/bin/python scripts/sprint13_expand_catalog.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "data" / "assets"
sys.path.insert(0, str(REPO / "src"))

from rosclaw_know.sim_ingest.cross_embodiment import (  # noqa: E402
    load_default_transfer_table,
)

# ── New FailureMode entries (4) ─────────────────────────────────────────


NEW_FAILURES: list[dict[str, Any]] = [
    {
        "id": "failure_unhandled_collision_contact",
        "name": "Unhandled Collision Contact",
        "domain": "Control_Locomotion",
        "symptom_text": (
            "A robot link makes unintended contact with the environment and the "
            "controller keeps driving the same trajectory through the obstacle."
        ),
        "normalized_symptom": "collision_contact_no_replan",
        "observable_signals": [
            "contact force > F_max sustained for multiple control steps",
            "trajectory tracking continues past first contact event",
            "no replan trigger fired after collision detection",
        ],
        "likely_causes": [
            "no collision response handler subscribed to contact stream",
            "environment model stale or incomplete",
            "safety margin set below sensor noise floor",
        ],
        "contraindications": [
            "do not just retry the same trajectory — replan with the updated world model",
        ],
        "severity": "safety_critical",
    },
    {
        "id": "failure_joint_limit_breach",
        "name": "Joint Limit Breach",
        "domain": "Control_Locomotion",
        "symptom_text": (
            "A joint position or torque crosses the URDF / controller limit because the "
            "planner emitted an out-of-range setpoint and the runtime did not clamp it."
        ),
        "normalized_symptom": "joint_position_limit_exceeded",
        "observable_signals": [
            "joint angle reports > qmax or < qmin for one or more steps",
            "torque command exceeds torque_max at the affected joint",
            "controller error flag raised at the joint level",
        ],
        "likely_causes": [
            "trajectory generator did not respect URDF joint limits",
            "IK solver returned an out-of-range solution that was sent verbatim",
            "missing post-IK joint-limit clamp in the runtime",
        ],
        "contraindications": [
            "do not just clamp inside the low-level driver — fix the planner so the limit is respected upstream",
        ],
        "severity": "safety_critical",
    },
    {
        "id": "failure_sensor_spike_dropout",
        "name": "Sensor Spike or Dropout",
        "domain": "Perception_Vision",
        "symptom_text": (
            "A sensor channel returns a value far outside its expected envelope (spike) "
            "or no value at all (dropout) and the value flows into the state estimator "
            "unfiltered."
        ),
        "normalized_symptom": "sensor_outlier_propagated_to_state",
        "observable_signals": [
            "sensor reading > 5σ from running mean for a single step",
            "consecutive frames of missing measurements not flagged",
            "state estimate jumps in lock-step with the bad reading",
        ],
        "likely_causes": [
            "no median / Hampel filter on the sensor pipeline",
            "no dropout detector raising a measurement_valid flag",
            "estimator trusts every sample equally regardless of innovation",
        ],
        "contraindications": [
            "do not blindly average across an outlier — flag it and let the planner decide",
        ],
        "severity": "warning",
    },
    {
        "id": "failure_safety_stop_no_recovery",
        "name": "Safety Stop Without Recovery Path",
        "domain": "Control_Locomotion",
        "symptom_text": (
            "Safety controller triggers a halt and the runtime has no defined "
            "supervised-resume path; the agent retries the offending command without "
            "operator clearance."
        ),
        "normalized_symptom": "safety_halt_blocks_resumption",
        "observable_signals": [
            "safety_stop event followed by repeated identical command retries",
            "no operator-clearance acknowledgement recorded before resumption",
            "post-halt state divergent from pre-halt plan assumptions",
        ],
        "likely_causes": [
            "missing supervised-resume protocol in the runtime",
            "agent treats safety_stop as a transient error",
            "no state-resync step between halt and resume",
        ],
        "contraindications": [
            "do not auto-resume after a safety halt without operator acknowledgement",
        ],
        "severity": "safety_critical",
    },
]


# ── New FixPattern nodes (7) ────────────────────────────────────────────


NEW_FIX_PATTERNS: list[dict[str, Any]] = [
    {
        "id": "compiled_collision_avoidance_replan",
        "failure_ids": ["failure_unhandled_collision_contact"],
        "domain": "Control_Locomotion",
        "fix_summary": (
            "On a collision event, abort the in-flight trajectory, re-snapshot the "
            "environment, and replan from the safe-stop pose before resuming."
        ),
        "preconditions": [
            "collision detector emits a contact event with location / normal",
            "a planner exists that can re-solve from the safe-stop pose",
        ],
        "implementation_steps": [
            "- Subscribe to the collision/contact stream and gate command publication on a `collision_clear` flag.",
            "- On contact, push a safe-stop pose and refresh the obstacle map before calling the planner again.",
            "```python\n# Pseudo-loop\nif collision_event:\n    runtime.publish_safe_stop()\n    world_model.refresh()\n    plan = planner.replan(from_pose=runtime.current_pose())\n```",
        ],
        "code_targets": ["collision response handler", "replanner entry point"],
        "expected_verifier_signals": [
            "no second contact event on the same obstacle within the same episode",
            "trajectory completion rate recovers after the replan",
        ],
        "anti_patterns": [
            "Retrying the failing trajectory without updating the world model.",
        ],
        "source_ids": ["sprint13_seed::collision_recovery"],
    },
    {
        "id": "compiled_joint_limit_planner_clamp",
        "failure_ids": ["failure_joint_limit_breach"],
        "domain": "Control_Locomotion",
        "fix_summary": (
            "Apply the URDF joint limit envelope at the planner output, not just at "
            "the low-level driver — the planner sees the constraint and uses it to "
            "pick feasible IK branches."
        ),
        "preconditions": [
            "URDF joint limits are available to the planner",
            "the planner can backtrack when a candidate setpoint is infeasible",
        ],
        "implementation_steps": [
            "- Inject the joint limit set into the IK solver's constraint list.",
            "- Reject any candidate trajectory whose first violating step falls inside the limit envelope plus a safety margin.",
            "```python\nfor q in candidate_traj:\n    if any(q[i] < qmin[i] + safety or q[i] > qmax[i] - safety for i in range(dof)):\n        raise InfeasibleSetpoint(joint=i)\n```",
        ],
        "code_targets": ["IK solver constraint set", "trajectory feasibility check"],
        "expected_verifier_signals": [
            "joint_limit_violation event count drops to zero across the suite",
            "no torque saturation events at the affected joint within the planning horizon",
        ],
        "anti_patterns": [
            "Clamping silently inside the low-level driver and hiding the bug upstream.",
        ],
        "source_ids": ["sprint13_seed::joint_limit_planner_fix"],
    },
    {
        "id": "compiled_sensor_median_filter_guard",
        "failure_ids": ["failure_sensor_spike_dropout"],
        "domain": "Perception_Vision",
        "fix_summary": (
            "Wrap each noisy sensor channel in a Hampel / rolling-median filter that "
            "rejects single-step >5σ outliers, and emit a measurement_valid flag the "
            "estimator can downweight."
        ),
        "preconditions": [
            "a rolling-window of recent sensor samples is available",
            "the estimator can consume a per-sample validity flag",
        ],
        "implementation_steps": [
            "- Replace direct sensor reads with a Hampel filter that returns `(value, is_outlier)`.",
            "- Have the estimator multiply the innovation by `0.0 if is_outlier else 1.0`.",
            "```python\nv, outlier = hampel_filter(channel, window=21, k=3.0)\nif outlier:\n    innovation_weight = 0.0\n```",
        ],
        "code_targets": ["sensor preprocessing layer", "estimator innovation weighting"],
        "expected_verifier_signals": [
            "state-estimate variance no longer correlates with single-sample sensor spikes",
            "downstream control jitter drops measurably",
        ],
        "anti_patterns": [
            "Averaging across the outlier window — washes out the spike but also the real signal.",
        ],
        "source_ids": ["sprint13_seed::sensor_outlier_guard"],
    },
    {
        "id": "compiled_safety_stop_supervised_resume",
        "failure_ids": ["failure_safety_stop_no_recovery"],
        "domain": "Control_Locomotion",
        "fix_summary": (
            "Define an explicit supervised-resume protocol: after a safety stop, the "
            "agent must request an operator acknowledgement and re-sync state before "
            "the next command is published."
        ),
        "preconditions": [
            "an operator-clearance channel exists (UI button / signed token / supervisor service)",
            "the state estimator can re-sync from sensor truth on demand",
        ],
        "implementation_steps": [
            "- On `safety_stop`, transition the runtime to a `HALTED` state and refuse to publish further commands.",
            "- Require an `operator_clear` event before transitioning to `RESUMING`, then run a state re-sync, then resume.",
            "```python\nif event == 'safety_stop':\n    runtime.state = 'HALTED'\n# blocking — waits for operator\nawait operator_clear_event()\nstate.resync()\nruntime.state = 'RESUMING'\n```",
        ],
        "code_targets": ["runtime state machine", "operator-clearance subscriber"],
        "expected_verifier_signals": [
            "no command publication between safety_stop and operator_clear in the trace",
            "post-resume state error within tolerance of pre-halt plan assumptions",
        ],
        "anti_patterns": [
            "Treating safety_stop as a transient error and retrying the same command.",
        ],
        "source_ids": ["sprint13_seed::safety_stop_recovery"],
    },
    {
        "id": "compiled_mpc_replan_on_state_error",
        "failure_ids": ["failure_planning_divergence"],
        "domain": "Planning_Decision",
        "fix_summary": (
            "Replace open-loop trajectory following with an MPC loop that re-solves "
            "the optimisation every dt using the latest measured state, so plan and "
            "trajectory never drift far apart."
        ),
        "preconditions": [
            "the optimisation problem solves within one control period",
            "a state estimator publishes a fresh measurement every dt",
        ],
        "implementation_steps": [
            "- Replace the static trajectory follower with an MPC loop bounded by horizon H and step dt.",
            "- At each step: read measured state, re-solve the QP, publish the first command, discard the rest.",
            "```python\nfor _ in range(steps):\n    x = state_estimator.read()\n    u = mpc.solve(x, horizon=H, dt=dt)\n    runtime.publish(u[0])\n```",
        ],
        "code_targets": ["trajectory follower loop", "MPC solver wrapper"],
        "expected_verifier_signals": [
            "position error stops growing monotonically",
            "tracking error stays bounded across the full episode",
        ],
        "anti_patterns": [
            "Stiffening feed-forward gains without addressing the open-loop drift root cause.",
        ],
        "source_ids": ["sprint13_seed::mpc_replan"],
    },
    {
        "id": "compiled_kv_cache_sliding_window",
        "failure_ids": ["failure_kv_cache_unbounded_growth"],
        "domain": "Memory_Reasoning",
        "fix_summary": (
            "Cap the per-layer KV cache at a sliding window of N tokens and evict the "
            "oldest entries on each forward pass; keep an optional global-attention "
            "sink for long-context recall."
        ),
        "preconditions": [
            "the attention kernel supports a variable-length KV cache",
            "the planner can tolerate a bounded context window",
        ],
        "implementation_steps": [
            "- Fix `KV_WINDOW = N`.  On each forward, drop entries beyond the window.",
            "- Optionally retain a fixed 'sink' prefix of K tokens that never evicts.",
            "```python\nif kv.size > KV_WINDOW:\n    kv = kv[-KV_WINDOW:]\n# optional sink: kv = torch.cat([sink, kv[-(KV_WINDOW - SINK_LEN):]], dim=0)\n```",
        ],
        "code_targets": ["KV-cache accumulator", "attention forward pass"],
        "expected_verifier_signals": [
            "GPU memory rises sub-linearly with sequence length",
            "throughput stays stable past the previous OOM threshold",
        ],
        "anti_patterns": [
            "Cutting batch size to mask the leak — only delays the OOM by one batch.",
        ],
        "source_ids": ["sprint13_seed::kv_cache_window"],
    },
    {
        "id": "compiled_gradient_clip_norm",
        "failure_ids": ["failure_gradient_explosion"],
        "domain": "Learning_Training",
        "fix_summary": (
            "Apply per-step gradient clipping (`clip_grad_norm_`) at a tuned threshold "
            "before the optimiser step, plus an automatic learning-rate halving when "
            "the clipped norm fires for K consecutive steps."
        ),
        "preconditions": [
            "the optimiser exposes a step hook callable between backward and step",
            "training step is the bottleneck (so the clip cost is amortised)",
        ],
        "implementation_steps": [
            "- After `loss.backward()`, call `torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=GRAD_CLIP).`",
            "- Track how often the clip fires; if it fires for K steps in a row, halve `lr`.",
            "```python\nloss.backward()\nnorm = torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=GRAD_CLIP)\nif norm > GRAD_CLIP:\n    consecutive_clips += 1\n    if consecutive_clips >= K:\n        for g in optim.param_groups: g['lr'] *= 0.5\nelse:\n    consecutive_clips = 0\noptim.step()\n```",
        ],
        "code_targets": ["training step", "lr scheduler hook"],
        "expected_verifier_signals": [
            "grad-norm distribution capped at GRAD_CLIP",
            "no NaN in loss across a fixed-length training run",
        ],
        "anti_patterns": [
            "Catching NaN after the fact and zeroing it out — the optimiser moment buffers are already corrupted.",
        ],
        "source_ids": ["sprint13_seed::grad_clip_norm"],
    },
]


# ── Helpers ─────────────────────────────────────────────────────────────


def update_failure_taxonomy(path: Path) -> tuple[int, int]:
    """Append new FailureMode entries to ``failure_taxonomy.yaml``.

    Returns ``(added, skipped)``.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    existing_ids = {f["id"] for f in data["failures"]}
    added = 0
    skipped = 0
    for f in NEW_FAILURES:
        if f["id"] in existing_ids:
            skipped += 1
            continue
        data["failures"].append(dict(f, schema_version="2.0"))
        added += 1
    if added:
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
    return added, skipped


def update_physical_graph(path: Path) -> tuple[int, int, int]:
    """Append new FailureMode + FixPattern nodes and FIXES edges.

    Returns ``(failures_added, patterns_added, edges_added)``.
    """
    g = json.loads(path.read_text(encoding="utf-8"))
    existing = {n["id"] for n in g["nodes"]}

    failures_added = 0
    for f in NEW_FAILURES:
        if f["id"] in existing:
            continue
        payload = dict(f, schema_version="2.0")
        g["nodes"].append({
            "id": f["id"],
            "node_type": "FailureMode",
            "domain": f["domain"],
            "payload": payload,
        })
        existing.add(f["id"])
        failures_added += 1

    patterns_added = 0
    edges_added = 0
    existing_edges = {
        (e["source"], e["relation"], e["target"])
        for e in g["edges"]
    }
    for fp in NEW_FIX_PATTERNS:
        if fp["id"] not in existing:
            payload = dict(fp, schema_version="2.0")
            g["nodes"].append({
                "id": fp["id"],
                "node_type": "FixPattern",
                "domain": fp["domain"],
                "payload": payload,
            })
            existing.add(fp["id"])
            patterns_added += 1
        for fid in fp["failure_ids"]:
            edge_key = (fp["id"], "FIXES", fid)
            if edge_key in existing_edges:
                continue
            g["edges"].append({
                "relation": "FIXES",
                "weight": 1.0,
                "source": fp["id"],
                "target": fid,
                "key": 0,
            })
            existing_edges.add(edge_key)
            edges_added += 1

    if failures_added or patterns_added or edges_added:
        path.write_text(json.dumps(g, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return failures_added, patterns_added, edges_added


def emit_acceptance_report(out_dir: Path,
                           yaml_added: int, yaml_skipped: int,
                           graph_failures: int, graph_patterns: int,
                           graph_edges: int) -> dict[str, Any]:
    """Compute the post-expansion coverage and persist the report."""
    table = load_default_transfer_table()
    coverage = {
        et: list(pids) for et, pids in sorted(table.items())
    }
    canonical = {
        "collision", "safety_stop", "joint_limit_violation", "controller_error",
        "sensor_outlier", "task_timeout", "trajectory_deviation",
        "actuator_saturation",
    }
    covered = sorted(set(coverage) & canonical)
    uncovered = sorted(canonical - set(coverage))

    report = {
        "yaml_failures_added": yaml_added,
        "yaml_failures_skipped_existing": yaml_skipped,
        "graph_failures_added": graph_failures,
        "graph_fix_patterns_added": graph_patterns,
        "graph_fixes_edges_added": graph_edges,
        "auto_derived_transfer_table_after_expansion": coverage,
        "covered_event_types": covered,
        "uncovered_event_types": uncovered,
        "distinct_event_types": len(covered),
        "canonical_total": len(canonical),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sprint13_acceptance_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    md_lines = ["# Sprint 13 — catalog expansion acceptance",
                "",
                f"- new FailureMode entries added to YAML: **{yaml_added}** "
                f"(skipped {yaml_skipped} already present)",
                f"- new FailureMode nodes added to graph: **{graph_failures}**",
                f"- new FixPattern nodes added to graph: **{graph_patterns}**",
                f"- FIXES edges added to graph: **{graph_edges}**",
                "",
                f"## Coverage: {len(covered)}/{len(canonical)} event_types",
                "",
                "| event_type | covered? | fix patterns |",
                "|---|---|---|"]
    for et in sorted(canonical):
        pids = coverage.get(et, [])
        mark = "✅" if pids else "❌"
        md_lines.append(f"| {et} | {mark} | {', '.join(pids) or '—'} |")
    md_lines.append("")
    md_lines.append("## How this happens")
    md_lines.append("")
    md_lines.append(
        "Sprint 10 made the `event_type → pattern_id` transfer table "
        "an auto-derived join over `FailureMode.id / normalized_symptom / "
        "name` ↔ `FixPattern.failure_ids`. Sprint 13 widens that join "
        "domain-side: with FixPatterns for all eight event_types' "
        "anchor failures, the table now covers the full canonical set."
    )
    (out_dir / "sprint13_acceptance_report.md").write_text(
        "\n".join(md_lines), encoding="utf-8"
    )
    return report


def main() -> int:
    yaml_added, yaml_skipped = update_failure_taxonomy(
        ASSETS / "failure_taxonomy.yaml"
    )
    g_failures, g_patterns, g_edges = update_physical_graph(
        ASSETS / "physical_graph.json"
    )
    report = emit_acceptance_report(
        ASSETS, yaml_added, yaml_skipped, g_failures, g_patterns, g_edges,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
