"""Curated, hand-written safety patterns.

These are the named patterns that rosclaw-how's SAFETY_RULES referenced
verbatim — e.g. `[anti_windup_pid]`, `[sliding_window_kv_cache]`,
`[gradient_clipping]`. They are NOT mined from the wiki; they are written
once and persisted across Muse runs so SeekDB always serves them.

Each entry produces:
  * one row in bridge_index.json's symptom_clusters
  * one file in code_patterns/

Bridge-index entries here also carry a ``safety_label`` field whose values
mirror rosclaw-how's normalize_error() output. That lets the runtime do an
exact-match shortcut before falling back to vector search.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CuratedPattern:
    """A hand-curated symptom+fix pair that always ships with the assets."""

    pattern_id: str               # used as cluster id AND code_patterns filename stem
    safety_label: str             # matches rosclaw-how's normalize_error labels
    standard_name: str            # human-readable symptom (= bridge_index standard_name)
    domain: str                   # one of FRONTIER_DOMAINS
    matched_keywords: list[str]   # words the agent's error log is likely to contain
    fix_pattern: str              # what the agent should do
    failed_attempt: str           # the anti-pattern
    before_code: str              # short Python excerpt — the "bad" version
    after_code: str               # the same excerpt with the fix applied
    cross_domain_hints: list[dict[str, str]]
    """Pre-baked cross-domain analogies — used when Muse hasn't yet produced
    organic ones for this safety label. Each dict has source_domain, insight,
    action_suggestion. neighbor_id can be omitted."""


CURATED_SAFETY_PATTERNS: list[CuratedPattern] = [
    CuratedPattern(
        pattern_id="anti_windup_pid",
        safety_label="Torque_Overflow",
        standard_name="PID integral wind-up drives actuator into torque saturation",
        domain="Control_Locomotion",
        matched_keywords=[
            "torque", "overflow", "saturation", "wind-up", "windup",
            "anti-windup", "pid", "integral", "actuator",
        ],
        fix_pattern=(
            "Apply conditional integration: stop accumulating the integral term whenever "
            "the actuator output is saturated AND the error direction would push further "
            "into saturation. Clamp `tau_cmd` with `torch.clamp(tau, -tau_max, tau_max)`."
        ),
        failed_attempt=(
            "Cranking up Kp/Ki to fix a tracking error during saturation — this only "
            "deepens the wind-up and amplifies the eventual oscillation when the load "
            "reverses direction."
        ),
        before_code=(
            "def pid_step(err, dt):\n"
            "    integ += err * dt           # unconditional integration\n"
            "    tau = Kp*err + Ki*integ + Kd*derr\n"
            "    return tau                  # no output limiter\n"
        ),
        after_code=(
            "def pid_step(err, dt):\n"
            "    tau_uncl = Kp*err + Ki*integ + Kd*derr\n"
            "    tau = torch.clamp(tau_uncl, -tau_max, tau_max)\n"
            "    saturated = tau != tau_uncl\n"
            "    if not (saturated and same_sign(err, tau_uncl)):\n"
            "        integ += err * dt        # conditional integration\n"
            "    return tau\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Systems_Compute",
                "insight": "Same back-pressure principle as bounded-queue producer-consumer: stop the producer when downstream is full.",
                "action_suggestion": "Treat actuator saturation as a back-pressure signal and pause the integrator the way you'd pause a queue writer.",
            },
            {
                "source_domain": "Learning_Training",
                "insight": "Clamp gradient analogue — gradient clipping prevents one outlier from blowing up a step, just as anti-windup prevents one saturated cycle from poisoning the next.",
                "action_suggestion": "Reuse the `clip_grad_norm_` mental model: an upper bound that fires only when the magnitude exceeds a known physical limit.",
            },
        ],
    ),
    CuratedPattern(
        pattern_id="sliding_window_kv_cache",
        safety_label="Memory_Exhaustion",
        standard_name="Unbounded KV-cache growth during long-horizon LLM rollouts causes CUDA OOM",
        domain="Memory_Reasoning",
        matched_keywords=[
            "memory", "exhaustion", "oom", "out of memory", "cuda",
            "kv-cache", "kv cache", "sequence", "long horizon",
        ],
        fix_pattern=(
            "Cap the per-layer KV tensor at a fixed window N (e.g. 256–512 tokens). "
            "On each forward, evict the oldest key/value rows. Keep an optional "
            "global-attention sink (the first M tokens) to preserve task context."
        ),
        failed_attempt=(
            "Increasing `--gpu-memory-utilization` or moving to a larger GPU — this only "
            "buys one more batch before the same overflow returns at a longer trajectory."
        ),
        before_code=(
            "k_cache.append(k_new)            # grows forever\n"
            "v_cache.append(v_new)\n"
            "attn = compute_attention(q, k_cache, v_cache)\n"
        ),
        after_code=(
            "k_cache = (k_cache + [k_new])[-W:]   # sliding window of size W\n"
            "v_cache = (v_cache + [v_new])[-W:]\n"
            "if attention_sink_tokens:\n"
            "    k_cache = sink_keys + k_cache    # keep the global sink\n"
            "    v_cache = sink_vals + v_cache\n"
            "attn = compute_attention(q, k_cache, v_cache)\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Control_Locomotion",
                "insight": "Like an anti-windup clamp on an integrator: keep the size of the accumulating state finite, however long the run.",
                "action_suggestion": "Treat the KV-cache as the integral term of attention; bound it the same way a PID bounds the integrator.",
            },
        ],
    ),
    CuratedPattern(
        pattern_id="gradient_clipping",
        safety_label="Numerical_Instability",
        standard_name="NaN/Inf in loss or weights after a step explodes the gradient",
        domain="Learning_Training",
        matched_keywords=[
            "nan", "inf", "numerical instability", "loss explod",
            "gradient explod", "gradient clip", "learning rate",
        ],
        fix_pattern=(
            "Apply `torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)` "
            "before `optimizer.step()`. If NaN still appears, halve the learning rate "
            "and verify the loss does not feed `log(<=0)` or divide by zero."
        ),
        failed_attempt=(
            "Catching the NaN after it surfaces and zeroing it out — the bad direction has "
            "already corrupted the optimizer's moment buffers (Adam's m and v). Restart "
            "training instead of patching."
        ),
        before_code=(
            "loss.backward()\n"
            "optimizer.step()                # no clipping\n"
        ),
        after_code=(
            "loss.backward()\n"
            "torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)\n"
            "optimizer.step()\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Control_Locomotion",
                "insight": "Gradient clipping is the SGD analogue of an output limiter on a controller — bound the magnitude of every actuation.",
                "action_suggestion": "Set `max_norm` analogous to `tau_max`: derived from a physical/training-stability limit, not guessed.",
            },
        ],
    ),
    CuratedPattern(
        pattern_id="output_saturation_clamp",
        safety_label="Velocity_Divergence",
        standard_name="Commanded velocity diverges to ±∞ when the integrator has no clamp",
        domain="Control_Locomotion",
        matched_keywords=[
            "velocity", "diverg", "infinite", "explode",
            "saturation", "clamp", "limit",
        ],
        fix_pattern=(
            "Wrap every commanded velocity through `torch.clamp(v_cmd, -v_max, v_max)` "
            "where `v_max` is read from the platform's robot_specifications YAML. "
            "Add an integral-leak term (`integ *= 0.99` per step) when in steady state."
        ),
        failed_attempt=(
            "Adding only a soft-start ramp on the user-side command — once internal "
            "feedback diverges, the ramp can't stop the integrator alone."
        ),
        before_code=(
            "v_cmd = pid_step(error, dt)     # unbounded output\n"
        ),
        after_code=(
            "v_cmd_raw = pid_step(error, dt)\n"
            "v_cmd = torch.clamp(v_cmd_raw, -V_MAX, V_MAX)\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Memory_Reasoning",
                "insight": "Same as the sliding-window KV-cache: cap the magnitude of the running state, not just the input.",
                "action_suggestion": "Bound the integrator state itself (`integ = clamp(integ, -I_MAX, I_MAX)`), mirroring the KV sliding window.",
            },
        ],
    ),
    CuratedPattern(
        pattern_id="closed_loop_replanning",
        safety_label="Oscillation_Divergence",
        standard_name="Open-loop plan tracks ground truth poorly when latency exceeds 50 ms",
        domain="Planning_Decision",
        matched_keywords=[
            "oscillat", "diverg", "tracking", "drift", "latency",
            "open-loop", "open loop",
        ],
        fix_pattern=(
            "Replace the open-loop planner with a Model-Predictive Control loop: "
            "re-solve a horizon-H optimization every dt using the latest measurement, "
            "execute only the first action, then re-solve. Keep dt ≤ system latency."
        ),
        failed_attempt=(
            "Compensating for tracking error by adding feed-forward terms tuned offline — "
            "the offline tuning never anticipates the actual disturbance profile."
        ),
        before_code=(
            "plan = solve_once(initial_state)   # open loop\n"
            "for u in plan:\n"
            "    execute(u)\n"
        ),
        after_code=(
            "while not done:\n"
            "    state = sense()                # close the loop\n"
            "    plan  = solve_horizon(state, H)\n"
            "    execute(plan[0])               # discard the rest, re-solve\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Learning_Training",
                "insight": "Same closed-loop principle as supervised → RL fine-tuning: don't trust your offline model, re-measure under the deployment distribution.",
                "action_suggestion": "Treat each MPC step as a one-step on-policy correction, the way RL fine-tuning corrects a supervised base.",
            },
        ],
    ),
    CuratedPattern(
        pattern_id="ppo_entropy_collapse_guard",
        safety_label="Entropy_Collapse",
        standard_name="PPO entropy crashes to zero and the policy fixates on a degenerate action",
        domain="Learning_Training",
        matched_keywords=[
            "ppo", "entropy", "collapse", "policy", "value loss",
            "degenerate", "episodes", "kl", "exploration",
        ],
        fix_pattern=(
            "Hold a minimum entropy bonus (coefficient ≥ 0.01) throughout training; add a "
            "target-KL trust region (early-stop the inner update when "
            "`mean_kl > 1.5 * target_kl`); and decay the learning rate (linear or "
            "cosine) so late-stage updates can't overrun the cliff. Reset the "
            "advantage running mean every N epochs to avoid frozen normalisation."
        ),
        failed_attempt=(
            "Cranking the policy LR or removing the entropy bonus to 'commit' to the "
            "current winner — accelerates the collapse and corrupts the value-function "
            "moments so even a restart shows the same degenerate basin."
        ),
        before_code=(
            "loss = policy_loss + 0.5 * value_loss   # no entropy bonus\n"
            "loss.backward()\n"
            "optimizer.step()                          # no KL early-stop, no LR decay\n"
        ),
        after_code=(
            "ent_coef = max(0.01, 0.02 - 1e-6 * step)\n"
            "loss = policy_loss + 0.5 * value_loss - ent_coef * entropy\n"
            "loss.backward()\n"
            "if mean_kl > 1.5 * target_kl:             # target-KL trust region\n"
            "    break                                  # early-stop the inner PPO epoch\n"
            "for g in optimizer.param_groups:           # linear LR decay\n"
            "    g['lr'] = lr0 * (1 - step / total_steps)\n"
            "optimizer.step()\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Control_Locomotion",
                "insight": "Same family as anti-windup: bound the magnitude of the update step the way you bound the actuator — KL = LR-equivalent for policies.",
                "action_suggestion": "Treat target-KL as the policy's `tau_max`; stop the inner loop the moment KL exceeds it.",
            },
            {
                "source_domain": "Memory_Reasoning",
                "insight": "Entropy bonus plays the role of an attention sink: a tiny always-on term that prevents the distribution from collapsing onto one mode.",
                "action_suggestion": "Keep a floor on the entropy coefficient the way KV-cache keeps `sink_tokens` — never decay it to zero.",
            },
        ],
    ),
    CuratedPattern(
        pattern_id="exponential_backoff_retry",
        safety_label="Communication_Timeout",
        standard_name="Network/RPC timeout cascades cause request storms after a partial outage",
        domain="Systems_Compute",
        matched_keywords=[
            "timeout", "timed out", "deadline exceeded",
            "retry", "rpc", "grpc",
        ],
        fix_pattern=(
            "Wrap network calls with exponential backoff (base 0.5 s, factor 2, jitter ±30 %), "
            "cap retries at 5, and add a circuit-breaker that opens when error rate > 50 % "
            "over the last 20 calls."
        ),
        failed_attempt=(
            "Tight `while True: retry()` loops — these turn a transient blip into a thundering herd."
        ),
        before_code=(
            "while True:\n"
            "    try:\n"
            "        return rpc.call()\n"
            "    except Timeout:\n"
            "        continue                    # tight retry\n"
        ),
        after_code=(
            "for attempt in range(MAX_ATTEMPTS):\n"
            "    try:\n"
            "        return rpc.call()\n"
            "    except Timeout:\n"
            "        delay = BASE * (2 ** attempt) * random.uniform(0.7, 1.3)\n"
            "        time.sleep(min(delay, MAX_BACKOFF))\n"
            "raise RpcUnreachable()\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Control_Locomotion",
                "insight": "Same idea as PID gain scheduling: tighten effort when the system responds, back off when it stalls.",
                "action_suggestion": "Lower the retry rate when failure rate climbs, just as a controller lowers gain in unstable regions.",
            },
        ],
    ),
]


def get_safety_label_map() -> dict[str, str]:
    """Returns ``safety_label → pattern_id`` so the runtime can do exact-match before vector search."""
    return {p.safety_label: p.pattern_id for p in CURATED_SAFETY_PATTERNS}


__all__ = ["CuratedPattern", "CURATED_SAFETY_PATTERNS", "get_safety_label_map"]
