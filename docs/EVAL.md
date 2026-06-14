# Phase 9 Real-Agent A/B Evaluation

This document describes how to run, extend, and interpret the Phase 9 real-agent
A/B harness introduced in `scripts/agent_eval_runner.py`.

## Quick start

```bash
# Synthetic smoke test — no API key, runs in seconds.
python scripts/agent_eval_runner.py \
  --backend synthetic --seeds 30 --label p9_synthetic_smoke

# Real LLM evaluation — requires DEEPSEEK_API_KEY in .env.
# For SiliconFlow, also set DEEPSEEK_BASE_URL=https://api.siliconflow.com
# and DEEPSEEK_MUSE_MODEL=<model/name>.
python scripts/agent_eval_runner.py \
  --backend llm --seeds 30 --label p9_llm --temperature 0.3

# Override model on the command line
python scripts/agent_eval_runner.py \
  --backend llm --model deepseek-ai/DeepSeek-V3 \
  --seeds 30 --label p9_llm_v3 --max-tokens 1200
```

Output lands in `data/benchmarks/phase9_real_agent/<label>/`.

## What Phase 9 measures

Phase 9 moves from the LLM-judge routing regression panel to **actual
control/coding agents**. Each task asks an agent to write a Python function;
that function is executed in a restricted sandbox against a deterministic
simulator and scored.

The harness reuses the Sprint 8 6-arm A/B framework:

- `baseline` — no hint.
- `true_know` — canonical know-how hint.
- `placebo_know` — unrelated hint of the same length.
- `shuffled_know` — hint from a different task family.
- `task_pack_only` — Sprint 7 task pack pre-flight, no CATALYST.
- `task_pack_plus_catalyst` — task pack + CATALYST when stuck.

Pass criterion:

- ≥3 of 5 tasks show statistically significant uplift (`p < 0.1`) for
  `true_know` vs `baseline`.
- Average `true_know` lift ≥ +0.10 (task-normalised).
- No task shows significant negative lift.

## Add a new task

1. Create a YAML file under `data/eval_tasks/`.
2. Implement a scoring function in `src/rosclaw_know/agent_eval/synthetic_tasks.py`.
3. Optionally add synthetic stubs for CI in `TASK_STUBS`.
4. Add a test to `tests/test_agent_eval.py`.

### YAML schema

```yaml
task_id: unique_id
description: |
  Human-readable task prompt. Explain state shape and the required function
  signature.
entrypoint: control                # function name the agent must define
scoring_fn_name: score_unique_id   # function in synthetic_tasks.py
objective_direction: maximize      # or minimize
metric_name: score_name
max_iters: 20
params:
  dt: 0.05
  total_time: 5.0
  timeout: 5.0
canonical_hint: "..."              # true_know hint
placebo_hint: "..."                # unrelated hint
shuffled_hint: "..."               # hint from another task
task_pack_hint: "..."              # Sprint 7-style pack hint
```

### Agent function sandbox

Agent code runs in a restricted namespace with:

- a small whitelist of builtins (`abs`, `min`, `max`, `range`, `len`, etc.)
- `math`
- `rng` — a seeded `random.Random` instance

Do **not** use `import`, `open`, or any I/O. Code that times out, raises, or
fails to define the required `entrypoint` is marked invalid and excluded from
score computation.

## Choose a backend

| Backend | Purpose | Requirements |
|---|---|---|
| `synthetic` | CI smoke tests; deterministic stubs | none |
| `llm` | DeepSeek / OpenAI-compatible / SiliconFlow | `DEEPSEEK_API_KEY` in `.env` |
| `claude` | Anthropic Claude | `anthropic` SDK installed + key |

The LLM backend constructs a prompt from the task description and the hint
selected by the arm, then extracts the first fenced Python block.

### Reasoning models

Some models (e.g. `nex-agi/Nex-N2-Pro` via SiliconFlow) emit their chain-of-thought
in a separate `reasoning_content` field and only write the final answer in
`content`. If `max_tokens` is too small, the code is cut off and extraction fails.
Use `--max-tokens` to leave enough headroom after reasoning (typically 4000+
for code-generation reasoning models).

## Read a report

After a run, `data/benchmarks/phase9_real_agent/<label>/` contains:

- `results.jsonl` — one line per trial with `task_id`, `arm`, `seed`, `score`,
  `valid`.
- `trials.jsonl` — generated code per trial.
- `summary.json` — full `ab_harness.to_jsonable` payload.
- `summary.md` — human-readable per-arm rank table and acceptance gates.

Interpret `summary.md`:

- Lower `avg_rank` is better.
- `Δ_post_injection` is signed by `objective_direction`.
- `positive_delta_majority` and `significant_trend_count` are the main gates.

## CI gate

```bash
pytest tests/test_agent_eval.py -v
```

All tests run without API keys.
