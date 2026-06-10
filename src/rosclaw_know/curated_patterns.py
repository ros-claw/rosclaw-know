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
    topic_group: str | None = None
    """The runtime topic_group this curated cluster belongs to. iter4_p3
    (2026-06-10) — without this field, HOW's topic-filter routing
    (``topic_filter_path=top1``) excludes curated clusters entirely from the
    candidate pool whenever the query's topic_group is non-empty, because
    only synth/autodraft clusters carry topic_group from Muse extraction.

    The classic case is T_001 PIDTuning: query_topic_group=control-loop-stability
    admits 11 synth clusters but anti_windup_pid (curated, no topic_group) is
    not admitted at all — even though its cosine sim 0.5583 would have put it
    at #3 in the unfiltered top-K.

    Each value MUST be one of the existing topic_groups in HOW's bridge
    (e.g. control-loop-stability, llm-inference-efficiency,
    rl-training-stability, ...). Never invent new groups here — that
    fragments the routing pool. If a curated truly doesn't fit any existing
    group, leave None and accept reduced topic-filter reach (it can still
    win via safety_label exact match or rescue ≥ 0.60 sim).
    """


CURATED_SAFETY_PATTERNS: list[CuratedPattern] = [
    CuratedPattern(
        pattern_id="anti_windup_pid",
        topic_group="control-loop-stability",
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
        topic_group="llm-inference-efficiency",
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
        topic_group="rl-training-stability",
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
        topic_group="control-loop-stability",
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
        topic_group="llm-planning-and-reasoning",
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
        topic_group="rl-training-stability",
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
        pattern_id="multi_stage_cc_cv_fast_charging",
        topic_group="battery-and-energy-management",
        safety_label="Battery_Capacity_Fade",
        standard_name=(
            "Aggressive constant-current fast-charging accelerates capacity fade "
            "via lithium plating at high SOC on graphite or silicon anodes"
        ),
        domain="Systems_Compute",
        matched_keywords=[
            "lithium plating", "li-ion", "battery", "capacity fade",
            "fast charging", "fast charge", "soc", "state of charge",
            "anode", "graphite", "cc", "constant current", "cc-cv",
            "current taper", "pulse charging", "charging protocol",
            "c rate", "4c", "thermal", "calendar aging", "cycle aging",
        ],
        fix_pattern=(
            "Replace single-stage CC charging with a multi-stage protocol: keep "
            "constant current only below ~70 % SOC, then taper current as a "
            "function of SOC (e.g. I(SOC) = I_max * (1 - SOC)^0.5) before "
            "switching to constant-voltage hold. Add a temperature-aware "
            "current limiter that derates above 35 °C anode-surface temperature. "
            "Pulse-charging with short rest intervals also relieves lithium "
            "concentration gradients near the anode."
        ),
        failed_attempt=(
            "Holding the same 4C constant-current target through the whole "
            "10→80 % SOC window — once SOC > 70 % the anode-side overpotential "
            "drops below 0 V vs. Li/Li+ and metallic lithium plates onto the "
            "graphite surface, irreversibly consuming cyclable lithium."
        ),
        before_code=(
            "def fast_charge(cell, target_soc=0.80):\n"
            "    while cell.soc < target_soc:\n"
            "        cell.apply_current(4 * cell.capacity_Ah)  # 4C flat\n"
            "        cell.step(dt=1.0)\n"
        ),
        after_code=(
            "def fast_charge(cell, target_soc=0.80, taper_above_soc=0.70):\n"
            "    while cell.soc < target_soc:\n"
            "        if cell.surface_temp_C > 35.0:\n"
            "            i_lim = 1.0 * cell.capacity_Ah                  # thermal derate\n"
            "        elif cell.soc < taper_above_soc:\n"
            "            i_lim = 4.0 * cell.capacity_Ah                  # CC stage\n"
            "        else:\n"
            "            # SOC-dependent taper toward CV hold\n"
            "            i_lim = 4.0 * cell.capacity_Ah * (1 - cell.soc) ** 0.5\n"
            "        cell.apply_current(i_lim)\n"
            "        cell.step(dt=1.0)\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Control_Locomotion",
                "insight": (
                    "Same shape as gain-scheduled PID: full gain near the operating "
                    "point, taper as you approach the saturation boundary."
                ),
                "action_suggestion": (
                    "Derate the control effort (charging current) as the state "
                    "(SOC) approaches the unsafe regime where plating dominates."
                ),
            },
        ],
    ),
    CuratedPattern(
        pattern_id="simd_aes_ni_hardware_crypto",
        topic_group="cybersecurity-and-resilience",
        safety_label="Crypto_Throughput_Bottleneck",
        standard_name=(
            "Pure-software block-cipher round functions saturate CPU on "
            "SubBytes/MixColumns; throughput plateaus at <100 MB/s on x86_64"
        ),
        domain="Systems_Compute",
        matched_keywords=[
            "aes", "aes-ni", "aes128", "aes256", "throughput", "mb/s",
            "gb/s", "crypto", "cipher", "encryption", "decryption",
            "subbytes", "mixcolumns", "sbox", "round function",
            "intrinsics", "_mm_aesenc_si128", "vectorize", "simd",
            "avx", "sse", "pclmulqdq", "bitslice", "bitslicing",
            "hardware acceleration", "gcm", "ctr mode",
        ],
        fix_pattern=(
            "Replace the SBOX-table + ShiftRows + MixColumns inner loop with "
            "x86 AES-NI intrinsics (`_mm_aesenc_si128`, `_mm_aesenclast_si128`) — "
            "one AESENC instruction per round retires in ~3 cycles, delivering "
            "1-2 GB/s per core. Use `_mm_clmulepi64_si128` (PCLMULQDQ) for the "
            "GHASH multiply in GCM mode. For platforms without AES-NI, fall "
            "back to a bitsliced implementation (parallel 8-block lanes) "
            "rather than the scalar SBOX-table loop."
        ),
        failed_attempt=(
            "Hand-rolled SBOX lookup tables in C with `unsigned char state[16]` "
            "— even with `-O3` the compiler can't vectorize the 16-byte SBOX "
            "indirection, so the loop bottlenecks on L1 latency at ~80 MB/s."
        ),
        before_code=(
            "// scalar AES round — SBOX lookup serialises on L1 latency\n"
            "static void aes_round(uint8_t s[16], const uint8_t k[16]) {\n"
            "    for (int i = 0; i < 16; ++i) s[i] = SBOX[s[i]];\n"
            "    shift_rows(s);\n"
            "    mix_columns(s);\n"
            "    for (int i = 0; i < 16; ++i) s[i] ^= k[i];\n"
            "}\n"
        ),
        after_code=(
            "// AES-NI round — single instruction, ~3 cycle latency, ~1.5 GB/s\n"
            "#include <wmmintrin.h>\n"
            "static __m128i aes_round_ni(__m128i state, __m128i round_key) {\n"
            "    return _mm_aesenc_si128(state, round_key);\n"
            "}\n"
            "// GCM multiply: use PCLMULQDQ\n"
            "static __m128i ghash_mul(__m128i a, __m128i b) {\n"
            "    return _mm_clmulepi64_si128(a, b, 0x00);\n"
            "}\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Learning_Training",
                "insight": (
                    "Same insight as moving a softmax from Python to fused "
                    "CUDA kernels: when the hot path is a tight 16-byte loop, "
                    "the fix is hardware-specific intrinsics, not a smarter "
                    "algorithm."
                ),
                "action_suggestion": (
                    "Profile to confirm the bottleneck is the round function, "
                    "then drop to platform intrinsics. Keep a portable "
                    "fallback (bitsliced) for non-x86 / unprivileged targets."
                ),
            },
        ],
    ),
    CuratedPattern(
        pattern_id="time_optimal_path_blending",
        topic_group="locomotion-and-manipulation",
        safety_label="Robot_Cycle_Time_Inflation",
        standard_name=(
            "Joint-space trajectories that decelerate to zero at every via-point "
            "inflate cycle time by 40-60 % even when via-points are colinear"
        ),
        domain="Control_Locomotion",
        matched_keywords=[
            "trajectory", "cycle time", "via-point", "via point",
            "waypoint", "joint space", "joint-space", "pick and place",
            "pick-and-place", "robot arm", "manipulator", "deceleration",
            "topp", "time-optimal", "time optimal", "path parameterization",
            "trajectory blending", "via-point blending", "blend radius",
            "s-curve", "jerk limited", "jerk-limited", "trapezoidal",
            "motion planning", "dof", "throughput",
        ],
        fix_pattern=(
            "Use Time-Optimal Path Parameterization (TOPP-RA or equivalent) "
            "over the full multi-via-point path so the velocity profile is "
            "computed against joint torque/velocity/acceleration limits "
            "globally, not per-segment. For colinear via-points let the "
            "blender preserve a non-zero pass velocity (blend_radius > 0). "
            "Where TOPP is unavailable, fall back to jerk-limited S-curve "
            "profiles per segment with explicit blend-velocity continuity at "
            "via-points — the arm never stops at intermediate poses."
        ),
        failed_attempt=(
            "Calling MoveIt's joint_trajectory_controller with each via-point "
            "as a separate goal — the controller decelerates to zero at every "
            "intermediate pose because each goal is a stop-condition, even "
            "though the geometric path could be traversed at constant speed."
        ),
        before_code=(
            "for via in via_points:\n"
            "    # each call decelerates to zero at `via` — wastes 60% of cycle\n"
            "    arm.move_to(via, velocity_scale=1.0)\n"
        ),
        after_code=(
            "from toppra import TOPPRA\n"
            "# Compute a single time-optimal velocity profile across ALL via-points,\n"
            "# preserving non-zero pass velocity at colinear segments.\n"
            "profile = TOPPRA(\n"
            "    path=via_points,\n"
            "    vlim=arm.joint_velocity_limits,\n"
            "    alim=arm.joint_acceleration_limits,\n"
            "    blend_radius=0.05,                  # meters of blend overlap\n"
            ").compute_trajectory()\n"
            "arm.follow_trajectory(profile)         # never stops mid-path\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Planning_Decision",
                "insight": (
                    "Solving each via-point in isolation is the same anti-"
                    "pattern as greedy local search on a global schedule — "
                    "the local optimum (full deceleration at each goal) is "
                    "far from the global one (constant speed through colinear "
                    "segments)."
                ),
                "action_suggestion": (
                    "Plan the velocity profile over the FULL multi-via-point "
                    "path, not segment-by-segment. Hand the planner the "
                    "joint-level kinematic limits, not just the geometric "
                    "waypoints."
                ),
            },
        ],
    ),
    CuratedPattern(
        pattern_id="motion_blur_imu_aided_deblur",
        topic_group="3d-perception-and-mapping",
        safety_label="Image_Motion_Blur",
        standard_name=(
            "Onboard camera produces motion-blurred frames during fast "
            "platform motion; downstream detection / classification recall "
            "drops 40-60 % vs. stationary baseline"
        ),
        domain="Perception_Vision",
        matched_keywords=[
            "motion blur", "blur kernel", "exposure time", "exposure",
            "shutter", "rolling shutter", "global shutter", "iso",
            "imu", "imu-aided", "imu aided", "deblur", "deblurring",
            "wiener", "richardson lucy", "uav", "drone", "quadrotor",
            "flyby", "hover", "hover-and-stare", "inspection",
            "detection recall", "blur compensation", "image stabilization",
            "frame quality", "blur extent", "shutter speed",
        ],
        fix_pattern=(
            "Three layered fixes, applied in order of cost: (1) shorten "
            "shutter (e.g. 1/500 s) and bump ISO + denoise — eliminates blur "
            "at the source for fast platforms with adequate light. (2) "
            "Replace continuous flybys with hover-and-stare waypoints near "
            "the inspection target — at v≈0 the blur extent is zero. "
            "(3) When neither is feasible (low light, fixed mission profile), "
            "estimate a per-frame blur kernel from the IMU-predicted camera "
            "motion during the exposure window and Wiener-deconvolve or feed "
            "the predicted kernel to a learned deblur network (e.g. "
            "DeblurGAN, NAFNet)."
        ),
        failed_attempt=(
            "Naively bumping ISO without shortening exposure — blur stays "
            "the same but noise grows, and the detection network is now "
            "robust to neither. Or training only on stationary images and "
            "hoping the network generalizes to blur."
        ),
        before_code=(
            "# fixed exposure regardless of platform velocity → blur scales with v\n"
            "frame = camera.capture(exposure_s=1.0/120, iso=400)\n"
            "boxes = detector.predict(frame)\n"
        ),
        after_code=(
            "# adapt exposure to platform velocity AND deconvolve IMU-predicted blur\n"
            "v_rel = imu.estimate_rel_velocity(target_pose)\n"
            "expo_s = min(0.5 / (v_rel + 1e-3), 1.0/500)   # cap blur extent\n"
            "frame = camera.capture(exposure_s=expo_s, iso=auto)\n"
            "if v_rel > BLUR_THRESHOLD:\n"
            "    kernel = imu.predict_blur_kernel(camera, expo_s)\n"
            "    frame = wiener_deconvolve(frame, kernel)\n"
            "boxes = detector.predict(frame)\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Systems_Compute",
                "insight": (
                    "Same shape as adaptive batch sizing under variable load: "
                    "set the exposure (batch) based on the live state of the "
                    "system (platform velocity), not a fixed default."
                ),
                "action_suggestion": (
                    "Bind capture parameters to the IMU-estimated motion "
                    "rather than mission-time constants — the right exposure "
                    "for the hover phase is wrong for the flyby phase."
                ),
            },
        ],
    ),
    CuratedPattern(
        pattern_id="metaheuristic_combinatorial_escape",
        topic_group="scheduling-optimization",
        safety_label="Combinatorial_Local_Optimum",
        standard_name=(
            "Greedy / local-search on combinatorial scheduling (job-shop, "
            "TSP, VRP) stagnates after the descent phase; objective plateaus "
            "well above the known optimum"
        ),
        domain="Planning_Decision",
        matched_keywords=[
            "job-shop", "job shop", "jsp", "scheduling", "makespan",
            "dispatch rule", "tabu search", "simulated annealing",
            "genetic algorithm", "ga", "metaheuristic", "neighborhood",
            "local optimum", "local minimum", "plateau", "stagnation",
            "shift move", "swap move", "critical path", "disjunctive graph",
            "tsp", "vrp", "vehicle routing", "permutation", "abz5",
            "ft10", "la01", "combinatorial",
        ],
        fix_pattern=(
            "Replace pure greedy descent with a neighborhood-escape "
            "metaheuristic. For job-shop specifically: tabu search with "
            "critical-path moves (swap two consecutive operations on the "
            "critical path) — the tabu list of size ~7-10 prevents reversal "
            "cycles, and the critical-path restriction means every move "
            "directly attacks the bottleneck. Alternatives that also work: "
            "GA with operation-based or precedence-preserving crossover, OR "
            "simulated annealing with shift moves (insert an operation at a "
            "different position in the sequence). The deciding choice between "
            "them is implementation effort, not solution quality at the "
            "abz5 / ft10 / la0X benchmark scale."
        ),
        failed_attempt=(
            "Running the same greedy dispatch rule longer, or restarting it "
            "from a different seed — the rule converges to the same family "
            "of local optima because the move set never crosses the basin "
            "boundary."
        ),
        before_code=(
            "# greedy first-available — saturates around 1400 on abz5 (opt=1234)\n"
            "schedule = []\n"
            "for op in operations:\n"
            "    earliest = max(machine_avail[op.machine], job_avail[op.job])\n"
            "    schedule.append((op, earliest))\n"
            "    machine_avail[op.machine] = job_avail[op.job] = earliest + op.dur\n"
        ),
        after_code=(
            "# tabu search with critical-path moves — reaches 1234-1260 on abz5\n"
            "best = greedy_initial_schedule(operations)\n"
            "tabu = deque(maxlen=10)\n"
            "for _ in range(MAX_ITERS):\n"
            "    cp = critical_path(best)                       # bottleneck ops\n"
            "    moves = [(i, i+1) for i in range(len(cp)-1)\n"
            "             if (cp[i].job, cp[i+1].job) not in tabu]\n"
            "    cand = min((apply_move(best, m) for m in moves), key=makespan)\n"
            "    if makespan(cand) < makespan(best):\n"
            "        best = cand\n"
            "        tabu.append((cand.swapped_jobs))\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Learning_Training",
                "insight": (
                    "Tabu / SA / GA are to combinatorial search what "
                    "exploration noise + replay are to reinforcement "
                    "learning: structured escape from local optima."
                ),
                "action_suggestion": (
                    "Don't tune the greedy heuristic further; switch the "
                    "outer-loop algorithm to one with a non-trivial "
                    "neighborhood and a memory of where it's been."
                ),
            },
        ],
    ),
    CuratedPattern(
        pattern_id="exponential_backoff_retry",
        topic_group="fault-tolerant-compute",
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
    # ── Iteration 2 (2026-06-08): cold-coverage curated for wild panel ─
    CuratedPattern(
        pattern_id="terrain_aware_locomotion",
        topic_group="locomotion-and-manipulation",
        safety_label="Tracking_Error",
        standard_name=(
            "Legged-robot trot gait diverges on uneven or slippery terrain — "
            "foot-slip events compound center-of-mass tracking error each cycle "
            "until the robot falls"
        ),
        domain="Control_Locomotion",
        matched_keywords=[
            "quadruped", "legged", "trot", "gait", "terrain", "uneven",
            "slippery", "gravel", "foot slip", "swing foot", "contact",
            "phase", "stance", "locomotion", "fall", "trotting", "step",
            "robot", "ground", "compliance",
        ],
        fix_pattern=(
            "Pick one of three orthogonal mitigations matched to the failure mode: "
            "(a) FOOT-CONTACT-AWARE MPC — predict contact events 1-2 stance phases "
            "ahead and constrain swing-foot placement to minimize estimated slip; "
            "(b) DOMAIN-RANDOMIZED RL — re-train the policy with friction-coefficient "
            "and terrain-height perturbations so it doesn't overfit a clean ground model; "
            "(c) SLIP-DETECTION REFLEX — monitor IMU + leg-encoder residuals during each "
            "stance; on detected slip, preempt the planned swing foot trajectory and "
            "replan toward a stable footstep within the support polygon."
        ),
        failed_attempt=(
            "Cranking up joint-PD gains to chase the tracking error — this amplifies "
            "slip-induced impulses and accelerates fall, because the underlying issue "
            "is contact uncertainty, not actuator compliance."
        ),
        before_code=(
            "def step(obs):\n"
            "    foot_target = mpc_solve(obs.com_state)  # assumes flat ground\n"
            "    return foot_target\n"
        ),
        after_code=(
            "def step(obs):\n"
            "    foot_target = mpc_solve(obs.com_state,\n"
            "                            contact_pred=predict_contacts(obs))\n"
            "    if detect_slip(obs.imu_residual, obs.encoder_residual):\n"
            "        foot_target = replan_for_stable_footstep(obs.support_polygon)\n"
            "    return foot_target\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Learning_Training",
                "insight": (
                    "Domain randomization in RL training is the same idea as model-"
                    "mismatch robustification in classical MPC: don't overfit one "
                    "operating model."
                ),
                "action_suggestion": (
                    "If the controller assumes friction=0.7 and the test surface is "
                    "friction=0.3, augment training distributions accordingly."
                ),
            },
            {
                "source_domain": "Memory_Reasoning",
                "insight": (
                    "Slip detection via residual monitoring is analogous to anomaly "
                    "detection in time-series data — both watch for deviation from "
                    "expected dynamics."
                ),
                "action_suggestion": (
                    "Use the same threshold + windowing logic that you'd apply to a "
                    "Kalman innovation sequence."
                ),
            },
        ],
    ),
    CuratedPattern(
        pattern_id="flash_attention_tiled_softmax",
        topic_group="llm-inference-efficiency",
        safety_label="Memory_Exhaustion",
        standard_name=(
            "Transformer self-attention layer materializes the full NxN matrix in "
            "HBM at long context length, causing CUDA OOM and HBM-bandwidth-bound "
            "(not compute-bound) inference throughput"
        ),
        domain="Systems_Compute",
        matched_keywords=[
            "self-attention", "attention", "transformer", "HBM", "memory bandwidth",
            "long context", "8k", "16k", "context length", "tiled", "online softmax",
            "FlashAttention", "SRAM", "block", "compute-bound", "bandwidth-bound",
            "Q K V", "NxN", "matrix", "softmax", "OOM",
        ],
        fix_pattern=(
            "Adopt FlashAttention-style TILED ONLINE-SOFTMAX attention: split Q and "
            "K/V into blocks that fit in SRAM, compute partial softmax incrementally "
            "with rescaling, and never materialize the full attention matrix in HBM. "
            "As a SECONDARY win, the SRAM-resident softmax becomes compute-bound "
            "(good for arithmetic intensity). Fallback when tiled attention is "
            "unavailable: SLIDING-WINDOW attention or circular-buffer KV-cache "
            "truncation to cap the effective sequence length seen by the softmax."
        ),
        failed_attempt=(
            "Casting QK to fp16 to halve memory — saves only 2x and degrades accuracy; "
            "the real problem is the N^2 materialization, not precision. Also: bumping "
            "GPU memory limit is a non-fix that just delays the OOM."
        ),
        before_code=(
            "def attention(Q, K, V):\n"
            "    scores = Q @ K.transpose(-2,-1) / sqrt(dim)  # NxN materialized\n"
            "    return softmax(scores) @ V  # OOM at large N\n"
        ),
        after_code=(
            "def flash_attention(Q, K, V, block_size=128):\n"
            "    out = zeros_like(V)\n"
            "    for j in range(0, K.shape[-2], block_size):\n"
            "        Kj = K[..., j:j+block_size, :]\n"
            "        Vj = V[..., j:j+block_size, :]\n"
            "        out = online_softmax_update(out, Q, Kj, Vj)\n"
            "    return out\n"
        ),
        cross_domain_hints=[
            {
                "source_domain": "Memory_Reasoning",
                "insight": (
                    "Online softmax is mathematically a streaming reduction: "
                    "maintain (max, sum) statistics and rescale partials. Same "
                    "pattern as streaming variance (Welford's algorithm)."
                ),
                "action_suggestion": (
                    "Treat the softmax denominator as a running statistic updated "
                    "incrementally rather than recomputed."
                ),
            },
            {
                "source_domain": "Systems_Compute",
                "insight": (
                    "HBM-bandwidth-bound becomes compute-bound when working set "
                    "fits in SRAM. Same trade-off as cache-blocking in dense "
                    "linear algebra."
                ),
                "action_suggestion": (
                    "Size block_size to match L2/SRAM capacity divided by "
                    "matrix-element bytes."
                ),
            },
        ],
    ),
]


def get_safety_label_map() -> dict[str, str]:
    """Returns ``safety_label → pattern_id`` so the runtime can do exact-match before vector search."""
    return {p.safety_label: p.pattern_id for p in CURATED_SAFETY_PATTERNS}


__all__ = ["CuratedPattern", "CURATED_SAFETY_PATTERNS", "get_safety_label_map"]
