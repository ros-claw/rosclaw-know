#!/usr/bin/env python3
"""Closed-loop A/B verification against the Frontier-Engineering benchmark.

Runs the same task twice — once with bridge_index.json injected into the
agent's system prompt (treatment), once without (control). Captures both
outputs to data/benchmarks/frontier_eng_ab/ for human comparison.

This is the Phase 1 "core hypothesis" check: does procedural knowledge
actually move the needle on Frontier-Eng tasks?
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SCRIPTS))

from how_health import assert_how_healthy  # noqa: E402

from rosclaw_know.config import ASSETS_DIR, BENCHMARKS_DIR, ensure_dirs  # noqa: E402

# Frontier-Eng smoke suite — 10 tasks chosen to span the categories in
# the test outline §5.2 (control, sim, systems-perf, high-reliability,
# energy, combinatorial, structural, inspection).  Each task pairs a
# concrete failure scenario with a sharp evaluation_hint so a judge can
# score 0-10 against an unambiguous rubric.
BENCHMARK_SUITE = [
    {
        "task_id": "TASK_001_PIDTuning",
        "symptom": (
            "PID controller drives a robotic-arm joint into sustained oscillation when "
            "the integral term saturates. Latency from sensor to actuator is 30 ms."
        ),
        "evaluation_hint": "should mention anti-windup clamp / derivative-on-measurement / gain scheduling",
    },
    {
        "task_id": "TASK_002_QuadrupedGait",
        "symptom": (
            "A quadruped robot's trot-gait policy diverges on uneven terrain: foot "
            "slip events cause the center-of-mass tracking error to grow each cycle "
            "until the robot falls within ~3 seconds of stepping onto loose gravel."
        ),
        "evaluation_hint": (
            "should propose foot-contact-aware MPC OR domain-randomized RL training "
            "with terrain perturbations OR a slip-detection reflex that re-plans "
            "the swing foot trajectory"
        ),
    },
    {
        "task_id": "TASK_003_RobotArmCycleTime",
        "symptom": (
            "A 6-DOF pick-and-place robot arm has a 4.2 s cycle time but the customer "
            "needs <=3.0 s. Profiling shows 60% of the cycle is joint-space motion that "
            "decelerates to zero between via-points, even when the via-points are colinear."
        ),
        "evaluation_hint": (
            "should propose trajectory blending / time-optimal path parameterization "
            "(TOPP) / via-point smoothing so the arm never stops at intermediate poses, "
            "OR jerk-limited S-curve profiles that hold acceleration through blends"
        ),
    },
    {
        "task_id": "TASK_004_HighReliableSimulation",
        "symptom": (
            "A reliability simulation of a redundant power-electronics inverter needs "
            "to estimate p(failure) ~ 1e-8 per operating hour. Naive Monte Carlo on "
            "10^6 samples returns 0 failures and gives no useful estimate."
        ),
        "evaluation_hint": (
            "should propose importance sampling / subset simulation / cross-entropy "
            "method / splitting (RESTART) - i.e. a rare-event variance-reduction "
            "technique, NOT just 'run more samples'"
        ),
    },
    {
        "task_id": "TASK_005_AES128_Throughput",
        "symptom": (
            "An AES-128-CBC implementation in pure C achieves 80 MB/s on a modern "
            "x86_64 server, but the requirement is 1 GB/s. Profiling shows the inner "
            "SubBytes/MixColumns loop dominates."
        ),
        "evaluation_hint": (
            "should propose AES-NI / vectorized intrinsics (_mm_aesenc_si128) OR "
            "GCM/CTR mode with hardware PCLMULQDQ, AND mention bitslicing as the "
            "portable fallback when AES-NI is unavailable"
        ),
    },
    {
        "task_id": "TASK_006_FlashAttention",
        "symptom": (
            "A transformer's self-attention layer hits CUDA OOM at 8K context length "
            "because the NxN attention matrix is materialized in HBM.  Inference "
            "throughput is also bandwidth-bound, not compute-bound."
        ),
        "evaluation_hint": (
            "should propose FlashAttention-style tiled / online-softmax attention "
            "that keeps the softmax computation in SRAM and never materializes the "
            "full attention matrix, OR sliding-window / circular-buffer KV-cache "
            "truncation as a second-best"
        ),
    },
    {
        "task_id": "TASK_007_BatteryFastCharging",
        "symptom": (
            "A Li-ion fast-charging profile that delivers 4C constant-current from "
            "10%->80% SOC accelerates capacity fade to >2% per 100 cycles on "
            "graphite anodes, far above the 0.5%/100c spec."
        ),
        "evaluation_hint": (
            "should propose multi-stage CC-CV with SOC-dependent current taper, "
            "OR pulse charging with rest intervals, OR temperature-aware current "
            "limiting - the underlying mechanism is avoiding lithium plating at "
            "high SOC"
        ),
    },
    {
        "task_id": "TASK_008_JobShop_abz",
        "symptom": (
            "A job-shop scheduler on the abz5 benchmark instance produces makespans "
            "around 1400 (known optimum 1234) with a greedy dispatch rule and "
            "stagnates after 10K iterations of local search."
        ),
        "evaluation_hint": (
            "should propose tabu search with critical-path moves / disjunctive-graph "
            "neighborhood, OR a genetic-algorithm crossover designed for JSP "
            "(e.g. operation-based or precedence-preserving), OR simulated annealing "
            "with shift moves - NOT just 'try a better dispatch rule'"
        ),
    },
    {
        "task_id": "TASK_009_TopologyOptimization",
        "symptom": (
            "A SIMP-based topology optimizer for a 2D cantilever-beam compliance "
            "problem produces checkerboard artifacts and mesh-dependent solutions: "
            "halving the element size doubles the apparent number of struts."
        ),
        "evaluation_hint": (
            "should propose density filtering / sensitivity filtering / Helmholtz "
            "PDE filter / projection schemes (Heaviside / threshold) to enforce "
            "mesh-independent length scale and eliminate checkerboarding"
        ),
    },
    {
        "task_id": "TASK_010_UAVInspection",
        "symptom": (
            "A quadrotor inspecting a wind-turbine blade with an onboard RGB camera "
            "produces motion-blurred frames at the tip flyby (15 m/s relative "
            "velocity, 100 mm focal length, 1/120s shutter), so defect detection "
            "recall drops from 0.9 (stationary) to 0.4 (flyby)."
        ),
        "evaluation_hint": (
            "should propose either (a) shorter exposure with higher ISO / global-"
            "shutter sensor, (b) hover-and-stare waypoints replacing the continuous "
            "flyby, OR (c) per-frame deblurring (Wiener / RL-based) using IMU-"
            "predicted blur kernels"
        ),
    },
    # ── Home-turf panel (TASK_W_*) — 8 tasks each cleanly mapping to one
    # of the 7 curated safety patterns in bridge_index.json + 1 flash-
    # attention pattern.  Symptoms are written as realistic engineering
    # descriptions WITHOUT deliberately seeding pattern keywords, so the
    # retrieval has to find the match by its own (vector + keyword) means.
    # This panel exists to answer the question:
    #   "When the bridge HAS a relevant curated pattern, does the agent
    #    pick it up and answer correctly?"
    # which is orthogonal to the wild-distribution test that TASK_001-010
    # already covers.
    {
        "task_id": "TASK_W_001_KVCacheLongContext",
        "symptom": (
            "A code-assistant LLM serving multi-turn programming sessions accumulates "
            "32K tokens of conversation history after 20 turns, hitting the model's 16K "
            "context window. Per-turn latency grows from 800ms to 3.5s as the running "
            "state fills."
        ),
        "evaluation_hint": (
            "should propose sliding-window truncation of the running state, OR an "
            "explicit recent-turns retention policy with summary-of-older-turns, OR "
            "running-state compression — the underlying mechanism is bounded "
            "accumulation of stored history, NOT just 'use a bigger model'"
        ),
    },
    {
        "task_id": "TASK_W_002_GradExplosionRL",
        "symptom": (
            "A PPO policy network training on a robotic manipulation task shows the "
            "reward curve diverging to NaN around training step 50K.  Loss values "
            "were stable until step 47K then spiked to 1e8 within 200 updates; "
            "gradient norms in the actor head are unbounded."
        ),
        "evaluation_hint": (
            "should propose gradient clipping (clip_grad_norm / clip_grad_value) "
            "with a sensible max_norm (~1.0-5.0), OR layer-norm / smaller LR, "
            "AND mention monitoring grad norms going forward"
        ),
    },
    {
        "task_id": "TASK_W_003_NetRetryStorm",
        "symptom": (
            "A microservice fetching upstream API data sees 95% of calls succeed but "
            "5% return 503.  Naive retry-on-failure with 100ms fixed delay causes "
            "cascading load: when upstream is degraded, retries amplify traffic ~10x, "
            "prolonging the outage."
        ),
        "evaluation_hint": (
            "should propose exponential backoff with jitter, OR a circuit breaker / "
            "token-bucket rate limit, OR adaptive concurrency — the underlying "
            "mechanism is bounded retry pressure that decreases under load, NOT "
            "constant-interval retries"
        ),
    },
    {
        "task_id": "TASK_W_004_EntropyCollapsePPO",
        "symptom": (
            "A PPO agent training on a stochastic environment converges to a "
            "deterministic policy by episode 5000.  Action entropy drops to "
            "near zero, exploration ceases, and the policy gets stuck 30% below "
            "SOTA reward.  Hyperparameters were copied from a known-good benchmark."
        ),
        "evaluation_hint": (
            "should propose an entropy bonus term in the loss (entropy_coef ~0.01) "
            "AND/OR target-KL early stopping in the inner PPO epoch, OR an action-"
            "noise / temperature schedule — the underlying mechanism is preventing "
            "the policy distribution from collapsing onto one mode"
        ),
    },
    {
        "task_id": "TASK_W_005_ActuatorOvershoot",
        "symptom": (
            "A linear voice-coil actuator commanded with PID + feedforward overshoots "
            "its rated 25 N peak force by 18% during fast setpoint changes, triggering "
            "the mechanical end-stops and dropping the position lock.  The command "
            "signal itself briefly exceeds the rated peak before the safety relay "
            "trips."
        ),
        "evaluation_hint": (
            "should propose clamping the controller output to the actuator's rated "
            "range BEFORE it leaves the controller (output_saturation_clamp), OR a "
            "rate-limited / jerk-limited reference trajectory so the setpoint "
            "itself never demands above-rated force"
        ),
    },
    {
        "task_id": "TASK_W_006_PlanningDivergence",
        "symptom": (
            "A model-predictive controller running at 50 Hz computes a 2-second-"
            "horizon trajectory assuming nominal dynamics, but actual ground friction "
            "varies by 3x across the operating area.  By the planning horizon's end, "
            "the predicted state differs from the measured state by >20 cm, and "
            "tracking error accumulates unboundedly between re-plans."
        ),
        "evaluation_hint": (
            "should propose closed-loop replanning (shorter re-plan interval / "
            "receding-horizon refresh whenever predicted-vs-measured divergence "
            "exceeds threshold), OR online dynamics-model adaptation, OR robust-MPC "
            "with disturbance bound — NOT just 'tune the cost weights'"
        ),
    },
    {
        "task_id": "TASK_W_007_IntegrationWindup",
        "symptom": (
            "A flow-rate PID controller saturates its control valve fully open during "
            "a long demand transient.  After the setpoint returns to normal, the "
            "controller takes 8 seconds to release accumulated integrator state, "
            "causing 25% overshoot past target as the valve eventually closes."
        ),
        "evaluation_hint": (
            "should propose anti-windup (back-calculation / conditional integration / "
            "clamp the integrator when the actuator is saturated) so the integrator "
            "state doesn't accumulate while the controller is open-loop"
        ),
    },
    {
        "task_id": "TASK_W_008_AttentionMemoryOOM",
        "symptom": (
            "A transformer model serving 4K-context inference on a 24 GB GPU hits "
            "OOM at batch size 8.  Memory profiling shows the N×N attention "
            "probability matrix taking 12 GB just for the full sequence, scaling "
            "quadratically with context length."
        ),
        "evaluation_hint": (
            "should propose tiled / online-softmax attention (FlashAttention-style) "
            "that keeps the softmax computation in SRAM and never materializes the "
            "full attention matrix, OR memory-efficient attention via chunked "
            "computation"
        ),
    },
]


def _build_treatment_prompt(bridge_index_path: Path) -> str:
    """Legacy static digest — top-12 bridge entries concatenated as a prefix.

    Kept as a fallback for offline runs (``--no-via-how``).  The default
    treatment path goes through rosclaw-how ``/wiki/v1/prompt/build``,
    which does per-task semantic retrieval over the full bridge instead
    of a fixed prefix — see :func:`_build_treatment_via_how`.
    """
    if not bridge_index_path.exists():
        return ""
    data = json.loads(bridge_index_path.read_text(encoding="utf-8"))
    clusters = data.get("symptom_clusters", {})
    if not clusters:
        return ""
    # Compact, agent-friendly digest (full json is too noisy)
    lines = ["[ROSCLAW cross-domain heuristic index (digested)]"]
    for i, (node, info) in enumerate(clusters.items()):
        if i >= 12:
            break
        lines.append(f"- {info['standard_name']}  [{info['domain']}]")
        for ana in info["cross_domain_analogies"][:2]:
            lines.append(f"    • {ana['source_domain']} → {ana['insight']}")
    return "\n".join(lines)


def _build_treatment_via_how(
    symptom: str,
    *,
    how_base: str,
    api_key: str,
    snippet_mode: str | None = None,
) -> tuple[str, dict]:
    """Per-task treatment snippet from rosclaw-how ``/wiki/v1/prompt/build``.

    Sends the task symptom as ``error_log``, with flat previous_scores
    and iteration=5 to push the state router into CATALYST (FREE
    exploration would skip injection, SAFETY would short-circuit on
    keyword match — neither is the path we're stress-testing here).

    ``snippet_mode`` (when set) is forwarded as a top-level request
    field — used by how to pick the ``full`` vs ``lightweight`` snippet
    composition variant.  Defaults to ``None`` (don't send the field;
    how's default applies — currently ``full``) so existing benchmarks
    stay byte-identical.

    Returns (snippet, meta).  ``snippet`` is empty when the router
    didn't inject (FREE / similarity below floor / SAFETY with no key
    match) — callers should treat that as "bridge had no relevant
    knowledge for this task" rather than as a failure.
    """
    import urllib.error
    import urllib.request

    body: dict[str, object] = {
        "error_log": symptom,
        # Plateau scores + past-warmup iteration force CATALYST.
        "previous_scores": [0.10] * 5,
        "current_iteration": 5,
    }
    if snippet_mode is not None:
        body["snippet_mode"] = snippet_mode
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{how_base.rstrip('/')}/wiki/v1/prompt/build",
        data=payload,
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return "", {"error": f"HTTP {exc.code}: {exc.reason}"}
    except Exception as exc:  # noqa: BLE001
        return "", {"error": f"how unreachable: {exc}"}

    try:
        data = json.loads(body)
    except ValueError:
        return "", {"error": f"how returned non-JSON: {body[:200]}"}

    return data.get("prompt_snippet", "") or "", {
        "strategy": data.get("strategy"),
        "injected": data.get("injected"),
        "pattern_id": data.get("pattern_id"),
        "matched_symptom": data.get("matched_symptom"),
        "similarity": data.get("similarity"),
        "is_staging": data.get("is_staging"),
        "injection_id": data.get("injection_id"),
        "latency_ms": data.get("latency_ms"),
        "snippet_mode": data.get("snippet_mode"),
    }


def _call_glm_agent(
    symptom: str,
    treatment_context: str = "",
    *,
    temperature: float = 0.0,
    seed: int | None = None,
) -> str | None:
    """Synchronous GLM-4.7-Flash fallback for agent generation.

    Used when the primary 302.ai / DeepSeek provider returns a balance /
    account-level failure (HTTP 402) so the paired_ab run doesn't silently
    lose all agent responses.
    """
    api_key = os.environ.get("ROSCLAW_GLM_API_KEY")
    base_url = os.environ.get(
        "ROSCLAW_GLM_BASE_URL",
        "https://api.z.ai/api/paas/v4",
    )
    model = os.environ.get("ROSCLAW_GLM_MODEL", "GLM-4.7-Flash")
    if not api_key:
        return None

    import urllib.error
    import urllib.request

    system_prompt = (
        "You are a code-optimisation and debug agent for embodied-AI engineering. "
        "Given an engineering symptom, propose a concrete, code-level fix with safety constraints."
    )
    user_content = f"Engineering symptom:\n{symptom}\n"
    if treatment_context:
        user_content += (
            "\n\nThe ROSCLAW heuristic index has matched the following "
            "cross-domain analogies. Use them when relevant.\n"
            + treatment_context
            + "\n\nReturn a fix plan with: (1) immediate code change, (2) physical/safety "
              "constraints to enforce, (3) verification step."
        )

    payload_dict: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "max_tokens": 2000,
        "seed": int(seed) if seed is not None else 0,
        "stream": False,
    }
    payload = json.dumps(payload_dict).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    import time as _time
    import urllib.error

    last_err: str | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()[:200]
            last_err = f"[GLM fallback HTTP {exc.code}: {body}]"
            if exc.code == 429 or exc.code >= 500:
                if attempt < 2:
                    _time.sleep(2.0 * (2 ** attempt))
                    continue
            return last_err
        except Exception as exc:  # noqa: BLE001
            last_err = f"[GLM fallback failed: {exc}]"
            if attempt < 2:
                _time.sleep(2.0 * (2 ** attempt))
                continue
            return last_err
        else:
            choices = data.get("choices") or []
            if choices:
                content = (choices[0].get("message") or {}).get("content")
                if content:
                    return content
            return None
    return last_err

def _call_agent(
    symptom: str,
    treatment_context: str = "",
    *,
    temperature: float = 0.0,
    seed: int | None = None,
) -> str:
    """Call DeepSeek chat as a stand-in agent. Returns the raw assistant reply.

    The "agent" is intentionally simple — a single chat completion — because
    Phase 1 just needs to show that *prompting* the agent with the procedural
    index improves the answer quality, not that we have an autonomous loop yet.

    ``temperature`` defaults to 0.0 for the original single-seed flow.  Multi-
    seed evaluation should pass ``temperature>0`` (e.g. 0.3) so the model
    actually produces a distribution of outputs across runs — at temp 0 the
    DeepSeek output is near-deterministic and "5 seeds" collapse to a single
    sample with model-jitter as the only noise source.

    ``seed`` (when set) is forwarded as the ``seed`` field in the
    OpenAI-compatible payload so paired A/B runs across different
    arms can share LLM randomness — making the snippet variable the
    only signal that moves the answer.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    if not api_key:
        return "[mock-agent reply — DEEPSEEK_API_KEY not set]"

    import urllib.request

    system_prompt = (
        "You are a code-optimisation and debug agent for embodied-AI engineering. "
        "Given an engineering symptom, propose a concrete, code-level fix with safety constraints."
    )
    user_content = f"Engineering symptom:\n{symptom}\n"
    if treatment_context:
        user_content += (
            "\n\nThe ROSCLAW heuristic index has matched the following "
            "cross-domain analogies. Use them when relevant.\n"
            + treatment_context
            + "\n\nReturn a fix plan with: (1) immediate code change, (2) physical/safety "
              "constraints to enforce, (3) verification step."
        )

    # Two layers of retries:
    #   Outer loop ("budget_attempt"): if a reasoning model returns
    #   finish_reason="length" with content=None (it burned the budget on
    #   hidden reasoning_tokens), retry once with a much larger max_tokens.
    #   Inner loop ("transient_retry"): 302.ai's free step-3.7-flash tier
    #   throws sporadic HTTP 429 / "err_code -10003 Parameter error" even
    #   on perfectly-formed requests (load-balancer rejection on busy
    #   shard). Retry with exponential backoff so a single bad routing
    #   decision doesn't kill a paired_ab seed.
    import time as _time
    import urllib.error

    last_err = "unknown"
    for budget_attempt in range(2):
        max_tok = 8000 if budget_attempt == 0 else 16000
        payload_dict: dict[str, object] = {
            "model": os.environ.get("DEEPSEEK_MUSE_MODEL", "deepseek-chat"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": max_tok,
        }
        if seed is not None:
            payload_dict["seed"] = int(seed)
        payload = json.dumps(payload_dict).encode("utf-8")

        empty_content_signal = False
        for transient_retry in range(6):
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            body: str | None = None
            status: int | None = None
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    body = resp.read().decode("utf-8")
                    status = resp.status
            except urllib.error.HTTPError as exc:
                status = exc.code
                try:
                    body = exc.read().decode("utf-8")
                except Exception:  # noqa: BLE001
                    body = ""
                last_err = f"[HTTP {status}: {body[:200] if body else exc}]"
                if status == 429 or status >= 500:
                    if transient_retry < 5:
                        _time.sleep(2.0 * (2 ** transient_retry))  # 2,4,8,16,32,64s
                        continue
                    break  # exhausted
                # Non-transient 4xx (e.g. 401/403/400) — give up this budget
                break
            except Exception as exc:  # noqa: BLE001
                last_err = f"[agent call failed: {exc}]"
                if transient_retry < 5:
                    _time.sleep(2.0 * (2 ** transient_retry))
                    continue
                break

            # Body present. Parse and check for 302.ai-style in-body errors.
            try:
                data = json.loads(body)
            except (ValueError, TypeError) as exc:
                last_err = f"[agent parse failed: {exc}; body={body[:200]}]"
                break
            api_err = data.get("error")
            if isinstance(api_err, dict) and api_err.get("err_code") == -10003:
                last_err = f"[302ai parameter err: {api_err.get('message','')[:120]}]"
                if transient_retry < 5:
                    _time.sleep(2.0 * (2 ** transient_retry))
                    continue
                break
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content")
            finish = choice.get("finish_reason")
            if content:
                return content
            # Empty content → reasoning model ate budget; signal outer to retry bigger
            last_err = f"[agent empty content finish={finish}]"
            empty_content_signal = True
            break

        if not empty_content_signal:
            # Either non-transient failure or exhausted transient retries.
            # If the primary provider hit an account/balance failure, try the
            # GLM-4.7-Flash fallback so a single dead key doesn't void the run.
            if "Insufficient Balance" in last_err or "HTTP 402" in last_err:
                glm_reply = _call_glm_agent(
                    symptom,
                    treatment_context,
                    temperature=temperature,
                    seed=seed,
                )
                if glm_reply and not glm_reply.startswith("["):
                    return glm_reply
            return last_err
    return last_err


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=BENCHMARKS_DIR / "frontier_eng_ab")
    ap.add_argument("--bridge-path", type=Path, default=ASSETS_DIR / "bridge_index.json")
    ap.add_argument(
        "--via-how",
        dest="via_how",
        action="store_true",
        default=True,
        help=(
            "Fetch treatment snippet per task from rosclaw-how "
            "/wiki/v1/prompt/build (default). Requires the how server "
            "to be running and reachable."
        ),
    )
    ap.add_argument(
        "--no-via-how",
        dest="via_how",
        action="store_false",
        help=(
            "Fall back to the static top-12 bridge_index digest as the "
            "treatment context (offline mode, no how server)."
        ),
    )
    ap.add_argument(
        "--how-base",
        default=os.environ.get("ROSCLAW_HOW_BASE", "http://127.0.0.1:47820"),
    )
    ap.add_argument(
        "--how-api-key",
        default=os.environ.get("ROSCLAW_HOW_API_KEY", "rw_sk_dev_local"),
    )
    ap.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help=(
            "Sampling temperature for the agent call (default 0.0 preserves "
            "the original single-seed deterministic flow). Multi-seed runs "
            "should bump this to ~0.3 so different invocations actually "
            "sample different outputs."
        ),
    )
    ap.add_argument(
        "--snippet-mode",
        choices=["full", "lightweight"],
        default=None,
        help=(
            "Forwarded to rosclaw-how /wiki/v1/prompt/build as the "
            "snippet_mode field. None (default) lets the server's own "
            "default apply (currently 'full'). Only honored under --via-how."
        ),
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "Integer seed forwarded as the OpenAI-compatible ``seed`` field "
            "in the DeepSeek payload, so paired A/B runs across snippet-mode "
            "arms can share LLM randomness (cancelling sampling noise so the "
            "snippet variable is what's left). Default None preserves the "
            "original unseeded flow."
        ),
    )
    ap.add_argument(
        "--task-ids",
        nargs="*",
        default=None,
        help=(
            "Optional list of exact task_ids or regex patterns to filter "
            "BENCHMARK_SUITE down to. Empty / not set runs the full 18-task panel."
        ),
    )
    args = ap.parse_args()

    if args.via_how:
        try:
            assert_how_healthy(args.how_base, args.how_api_key)
        except RuntimeError as exc:
            print(f"[verify-frontier] {exc}", file=sys.stderr)
            return 2

    ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if not args.via_how:
        static_context = _build_treatment_prompt(args.bridge_path)
        print(f"[static digest] Bridge context loaded: {len(static_context.splitlines())} lines\n")
    else:
        static_context = None
        print(f"[via /prompt/build @ {args.how_base}] per-task semantic retrieval\n")

    report = []
    if args.task_ids:
        import re as _re
        patterns = [_re.compile(p) for p in args.task_ids]
        tasks = [
            t for t in BENCHMARK_SUITE
            if t["task_id"] in args.task_ids
            or any(p.search(t["task_id"]) for p in patterns)
        ]
        if not tasks:
            print(f"[verify] no tasks matched --task-ids {args.task_ids}")
            return 1
        print(f"[verify] filtered to {len(tasks)} of {len(BENCHMARK_SUITE)} tasks")
    else:
        tasks = list(BENCHMARK_SUITE)
    for task in tasks:
        if args.seed is not None:
            print(f"▶ {task['task_id']}  (seed={args.seed})")
        else:
            print(f"▶ {task['task_id']}")

        if args.via_how:
            treatment_context, meta = _build_treatment_via_how(
                task["symptom"],
                how_base=args.how_base,
                api_key=args.how_api_key,
                snippet_mode=args.snippet_mode,
            )
            strategy = meta.get("strategy", "?")
            pid = meta.get("pattern_id") or "-"
            sim = meta.get("similarity")
            sim_str = f"{sim:.3f}" if isinstance(sim, (int, float)) else "-"
            print(
                f"  /prompt/build  strategy={strategy} pattern_id={pid} sim={sim_str} "
                f"injected={meta.get('injected')}"
            )
        else:
            treatment_context = static_context or ""
            meta = {"strategy": "static_digest", "injected": bool(treatment_context)}

        control = _call_agent(
            task["symptom"], temperature=args.temperature, seed=args.seed
        )
        treatment = _call_agent(
            task["symptom"],
            treatment_context=treatment_context,
            temperature=args.temperature,
            seed=args.seed,
        )

        (args.out_dir / f"{task['task_id']}.control.txt").write_text(control, encoding="utf-8")
        (args.out_dir / f"{task['task_id']}.treatment.txt").write_text(treatment, encoding="utf-8")

        report.append(
            {
                "task_id": task["task_id"],
                "evaluation_hint": task["evaluation_hint"],
                "control_first_200": control[:200],
                "treatment_first_200": treatment[:200],
                "how_meta": meta,
            }
        )
        print(f"  control:    {control[:80]!r}…")
        print(f"  treatment:  {treatment[:80]!r}…\n")

    summary_path = args.out_dir / "summary.json"
    summary_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report → {summary_path}")
    print(
        "\nNow manually inspect each task's control vs treatment file pair and "
        "judge whether the treatment more clearly hits the evaluation hint."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
