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


# ── Source-type-specialised variants (Phase 10) ─────────────────────────
#
# Phase 8's awesome-list ingest hit a 34% conversion rate because every
# source — academic paper, tool README, blog tutorial — went through the
# same prompt above. Different source shapes carry different "engineering
# content" signals, so we now route per source_type:
#
#   paper     → arXiv abstract / academic-style write-up
#   repo      → GitHub README (tool / library / dataset)
#   tutorial  → blog post / lesson / walkthrough
#   web       → generic HTML page (closest to the original prompt)
#
# The harvester reads `source_type` (or its legacy alias `fetch_kind`)
# from the markdown frontmatter and looks up the appropriate prompt via
# :func:`extractor_prompt_for`.

EXTRACTOR_PROMPT_PAPER = """You are mining an academic paper or arXiv abstract for ONE concrete (symptom → fix_pattern) pair.

Academic papers state a problem (the engineering symptom) and propose a method (the fix). Find both:

1. symptom: The engineering challenge the paper attacks. Look at the *problem statement* and the *gap in prior work* the paper claims to fill. NOT the topic — the failure mode being addressed.
   GOOD: "Long-horizon VLN policies collapse on unseen layouts due to overfit to training scenes"
   BAD : "We study vision-language navigation"

2. domain: EXACTLY ONE from {Perception_Vision, Planning_Decision, Control_Locomotion, Learning_Training, Memory_Reasoning, Systems_Compute, World_Physics}. Pick where the *failure mode* lives, not where the paper claims novelty.

3. fix_pattern: The paper's actual contribution as a technique. Be specific enough to be actionable. Often this is in the paper's "Method" or "Approach" section summary.
   GOOD: "Hierarchical instruction decomposition with sub-goal verification using cross-attention"
   BAD : "We propose a new method that improves performance"

4. failed_attempt: The baseline the paper beats, or a specific anti-pattern it argues against. Empty string if not clearly stated.

Return ONLY valid JSON:
{"symptom": "...", "domain": "...", "fix_pattern": "...", "failed_attempt": "..."}
"""


EXTRACTOR_PROMPT_REPO = """You are mining a software-tool README (typically a GitHub project) for ONE concrete (symptom → fix_pattern) pair.

Tool READMEs describe a capability — extract the engineering pain it eliminates and the technique the tool uses.

1. symptom: The problem the tool exists to solve. Look at "Why this tool", "Motivation", or the first paragraph stating the gap.
   GOOD: "Modbus/TCP traffic in factory floors is unauthenticated, allowing replay attacks on PLCs"
   BAD : "This is a Python library"

2. domain: EXACTLY ONE label. For tools, the domain is wherever the tool *operates* — not where it's written.

3. fix_pattern: WHAT the tool does at the technique level. Reduce the README's "Features" list into one core mechanism.
   GOOD: "Static analysis of IEC-61131-3 PLC code via symbolic-execution data-flow graph traversal"
   BAD : "It has a CLI and a web UI"

4. failed_attempt: An approach the README explicitly criticises, or a class of bug the tool catches that earlier tools missed. Empty if absent.

Return ONLY valid JSON.
"""


EXTRACTOR_PROMPT_TUTORIAL = """You are mining a tutorial / lesson / blog walkthrough for ONE concrete (symptom → fix_pattern) pair.

Tutorials teach by showing a wrong path then the right path. Extract both.

1. symptom: The mistake or pitfall the author is warning against. Often introduced as "a common mistake is…" or "you might think X, but…".
   GOOD: "Tuning Ziegler-Nichols PID on a plant with significant dead time produces persistent oscillation"
   BAD : "How to use PID controllers"

2. domain: EXACTLY ONE label.

3. fix_pattern: The corrected approach the tutorial teaches. Usually has a code example or a step-by-step procedure.
   GOOD: "Use Cohen-Coon or Lambda tuning rules for dead-time-dominant processes; verify with a step response"
   BAD : "Use PID tuning"

4. failed_attempt: Verbatim or near-verbatim restatement of the wrong path the tutorial warns against. Tutorials often *explicitly* show the anti-pattern.

Return ONLY valid JSON.
"""


EXTRACTOR_PROMPT_WEB = """You are mining a generic web page for ONE concrete (symptom → fix_pattern) pair.

The page may be a vendor docs page, blog snippet, or a third-party article. Be conservative — if no engineering content surfaces, return all-null.

1. symptom: A failure mode or engineering challenge the page concretely addresses. If the page is just marketing or a TOC, return null.

2. domain: EXACTLY ONE label.

3. fix_pattern: A specific technique mentioned. If only generic claims ("we make it fast"), return null.

4. failed_attempt: An anti-pattern criticised. Empty string acceptable.

Return ONLY valid JSON. Prefer returning all-null to fabricating content.
"""


_SOURCE_TYPE_PROMPTS: dict[str, str] = {
    "paper":    EXTRACTOR_PROMPT_PAPER,
    "repo":     EXTRACTOR_PROMPT_REPO,
    "tutorial": EXTRACTOR_PROMPT_TUTORIAL,
    "web":      EXTRACTOR_PROMPT_WEB,
    # Aliases that may appear in legacy frontmatter (Phase 8 awesome_fetcher,
    # research_sources.py).
    "github_readme": EXTRACTOR_PROMPT_REPO,
    "html_text":     EXTRACTOR_PROMPT_WEB,
    "pdf_meta":      EXTRACTOR_PROMPT_PAPER,   # PDFs are nearly always papers
}


def extractor_prompt_for(source_type: str | None) -> str:
    """Return the appropriate extractor prompt for a frontmatter source_type.

    Falls back to the generic :data:`EXTRACTOR_PROMPT` when source_type is
    unset / unknown — that keeps legacy wiki/ pages working unchanged.
    """
    if source_type is None:
        return EXTRACTOR_PROMPT
    return _SOURCE_TYPE_PROMPTS.get(source_type.strip().lower(), EXTRACTOR_PROMPT)


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

__all__ = [
    "EXTRACTOR_PROMPT",
    "EXTRACTOR_PROMPT_PAPER",
    "EXTRACTOR_PROMPT_REPO",
    "EXTRACTOR_PROMPT_TUTORIAL",
    "EXTRACTOR_PROMPT_WEB",
    "PLANNER_PROMPT",
    "MUSE_PROMPT",
    "FRONTIER_DOMAINS",
    "extractor_prompt_for",
]
