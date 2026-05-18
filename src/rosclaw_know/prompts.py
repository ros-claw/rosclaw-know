"""Prompt templates for Planner / Harvester / Muse.

Domain taxonomy: we sub-divide the Frontier-Eng "Robotics_Control" bucket
into 7 embodied-AI sub-domains because nearly every page in the ROSClaw
wiki concerns robotics — without sub-buckets we never get cross-domain
edges and Muse compiler has nothing to synthesise.
"""
from __future__ import annotations

# Sub-domains that the Weaver/Muse layers use to identify "cross-domain" edges.
# Robotics-heavy because the wiki corpus is embodied-AI focused.
FRONTIER_DOMAINS: tuple[str, ...] = (
    "Perception_Vision",       # cameras, depth, segmentation, sensor processing
    "Planning_Decision",       # navigation policies, action prediction, hierarchical planning
    "Control_Locomotion",      # PID/MPC, gait, balance, manipulation actuation
    "Learning_Training",       # RL/IL, data augmentation, sim-to-real, curriculum
    "Memory_Reasoning",        # LLM context, KV-cache, chain-of-thought, planning history
    "Systems_Compute",         # GPU memory, latency, throughput, real-time scheduling
    "World_Physics",           # simulation, contact dynamics, fluid/material
)

EXTRACTOR_PROMPT = """You are a hardcore engineering optimization specialist mining a robotics/AI research wiki.

Your job: convert each page into ONE concrete (symptom → fix_pattern) pair, if any can be inferred. Even paper abstracts and project descriptions often imply a concrete engineering challenge and the technique that addresses it — extract THAT.

EXTRACTION RULES:
1. symptom: A specific failure mode, performance bottleneck, or engineering challenge the page addresses. Examples:
   - "Trajectory tracking drifts when sensor latency exceeds 50ms"
   - "Long horizon planning blows up GPU memory"
   - "Sim-to-real gap causes policy collapse on unseen terrain"
   - "VLN agent ignores landmark cues in long instructions"
   Prefer faults that are concretely actionable. Generic topics like "we study X" or "deep learning is hard" do NOT count.

2. domain: Choose EXACTLY ONE label from this enum (pick the BEST FIT):
   - Perception_Vision:   cameras, depth, segmentation, jitter, occlusion, semantic perception
   - Planning_Decision:   navigation policies, action prediction, hierarchical planning, goal selection
   - Control_Locomotion:  PID/MPC, gait, balance, manipulation, motor torque, anti-windup
   - Learning_Training:   RL/IL, data augmentation, sim-to-real, curriculum, fine-tuning, dataset quality
   - Memory_Reasoning:    LLM context, KV-cache, chain-of-thought, working/episodic memory, recall
   - Systems_Compute:     GPU memory, latency, throughput, real-time scheduling, kernel optimisation
   - World_Physics:       simulation, contact dynamics, fluid/material, articulation, friction
   Pick the SINGLE BEST fit — don't default to one bucket; really consider where the *failure mode* lives.

3. fix_pattern: A specific technique, algorithm, or architectural choice the page promotes. Concrete enough that a reader could turn it into code or a parameter change. Examples:
   - "Use sliding-window KV-cache with N=64 timesteps"
   - "Anti-windup clamp on PID integral term saturating above 0.8 * tau_max"
   - "Hierarchical policy: high-level planner + low-level controller"
   - "Synthetic-data augmentation via NeRF-based scene generation"

4. failed_attempt: An anti-pattern the page criticises, or a baseline it beats. Be brief. Can be empty string.

ONLY RETURN ALL-NULL IF:
- The page contains NO engineering content at all (e.g. it's a sponsor list, license, table of contents)
- You truly cannot identify any technique or challenge

Return ONLY valid JSON:
{"symptom": "...", "domain": "...", "fix_pattern": "...", "failed_attempt": "..."}

If genuinely empty:
{"symptom": null, "domain": null, "fix_pattern": null, "failed_attempt": null}
"""

PLANNER_PROMPT = """You are the lead architect for an embodied-AI engineering
debug agent. The user reports a fault. Decompose it into THREE technical probes,
each rooted in one of these sub-domains:
Perception_Vision, Planning_Decision, Control_Locomotion, Learning_Training,
Memory_Reasoning, Systems_Compute, World_Physics.

Each probe is a short, search-friendly query string. Pick the three MOST RELEVANT
sub-domains for the symptom — don't pad with irrelevant ones.

Return ONLY:
{"probes": ["probe 1", "probe 2", "probe 3"], "primary_domain": "..."}
"""

MUSE_PROMPT = """You are a cross-domain engineering analogy compiler for embodied AI.
Two engineering problems below come from DIFFERENT sub-domains.

Domain A ({domain_a}) symptom: {symptom_a}
Domain A fix that worked:       {fix_a}
Domain B ({domain_b}) symptom: {symptom_b}

In ONE SHORT sentence (<100 characters), explain how the mechanism from Domain A's
fix could inspire a solution for Domain B's symptom. Focus on the transferable
MECHANISM (e.g. "sliding-window truncation", "anti-windup clamp", "gain scheduling",
"closed-loop verification", "hierarchical decomposition"), NOT vague metaphor.

Reply with the single sentence only — no preamble, no quotation marks.
"""

__all__ = ["EXTRACTOR_PROMPT", "PLANNER_PROMPT", "MUSE_PROMPT", "FRONTIER_DOMAINS"]
