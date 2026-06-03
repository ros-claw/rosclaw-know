"""Sprint 3 收尾 synthetic-trajectory fixtures (importable from tests + scripts).

Hand-crafted baseline → candidate pairs that demonstrate every AES /
CUDA / scheduling detector added in Sprint 3 收尾.

The actual content lives here (not under ``scripts/``) so tests can
import it without putting the scripts directory on sys.path.  The
``scripts/extract_trajectory_patterns.py`` driver re-exports the
same module so ``--include-synthetic-corpus`` keeps working.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SyntheticTrajectory:
    """One baseline → candidate pair from the synthetic corpus."""

    task_name: str
    baseline: str
    candidate: str
    target_kinds: tuple[str, ...]


AES_BASELINE = """\
#include <cstdint>
class AES128 {
  uint8_t state[16];
  void Round(const uint8_t* key) {
    for (int i = 0; i < 16; ++i) {
      uint8_t x = state[i];
      uint8_t y = key[i];
      state[i] = (uint8_t)(x ^ y);
    }
  }
public:
  bool VerifyTag(const uint8_t* a, const uint8_t* b, int n) {
    for (int i = 0; i < n; ++i) if (a[i] != b[i]) return false;
    return true;
  }
};
"""

AES_CANDIDATE = """\
#include <cstdint>
class AES128 {
  static const uint8_t sbox[256];
  static const uint32_t TE0[256];
  static const uint32_t TE1[256];
  static const uint8_t Rcon[11];
  uint8_t state[16];
  void Round(const uint8_t* key) {
    #pragma unroll
    for (int i = 0; i < 16; ++i) {
      uint8_t x = state[i];
      uint8_t y = key[i];
      uint8_t mask = (uint8_t)-((x ^ y) >> 7);
      state[i] = (uint8_t)((x ^ y) ^ (mask & sbox[x]));
    }
  }
public:
  bool VerifyTag(const uint8_t* a, const uint8_t* b, int n) {
    return constant_time_compare(a, b, n);
  }
};
"""

CUDA_BASELINE = """\
import torch

def custom_kernel(Q, K, V):
    scores = torch.matmul(Q, K.transpose(-1, -2))
    attn = torch.softmax(scores, dim=-1)
    return torch.matmul(attn, V)
"""

CUDA_CANDIDATE = """\
import triton
import triton.language as tl

BLOCK_M = 64
BLOCK_N = 64
BLOCK_K = 32
num_warps = 8
num_stages = 3

@triton.jit
def attention_fused_kernel(Q_ptr, K_ptr, V_ptr, O_ptr):
    pid_m = tl.program_id(0)
    offs_m = pid_m * BLOCK_M + tl.arange(0, BLOCK_M)
    q_tile = tl.load(Q_ptr + offs_m, mask=offs_m < tl.cdiv(BLOCK_M, 1))
    k_tile = tl.load(K_ptr + offs_m)
    tl.async_copy(K_ptr, k_tile)
    cp.async.commit_group()
    if producer_warp:
        tl.async_copy(V_ptr, k_tile)
    scores = tl.dot(q_tile, k_tile)
    attn = tl.softmax(scores)
    out = tl.dot(attn, k_tile)
    tl.store(O_ptr + offs_m, out)
"""

SCHED_BASELINE = """\
def schedule(jobs):
    out = []
    for j in jobs:
        out.append(j)
    return out
"""

SCHED_CANDIDATE = """\
def schedule(jobs):
    # Reorder by shortest-processing-time (SPT) priority
    out = sorted(jobs, key=lambda j: j.processing_time)
    # Enforce precedence and resource_capacity
    for j in out:
        for p in j.predecessors:
            if not p.completed:
                continue
        if j.machine_available:
            j.start_time = max(j.earliest_start, j.due_date - j.processing_time)
    # Apply Johnson's rule for two-machine instances
    out = apply_johnson_rule(out)
    return out
"""


SYNTHETIC_TRAJECTORIES: tuple[SyntheticTrajectory, ...] = (
    SyntheticTrajectory(
        task_name="AES-128",
        baseline=AES_BASELINE,
        candidate=AES_CANDIDATE,
        target_kinds=(
            "add_lookup_table",
            "unroll_loop",
            "add_branchless_select",
            "add_constant_time_compare",
        ),
    ),
    SyntheticTrajectory(
        task_name="FlashAttention",
        baseline=CUDA_BASELINE,
        candidate=CUDA_CANDIDATE,
        target_kinds=(
            "add_shared_memory_tile",
            "adjust_block_size",
            "add_warp_specialization",
            "add_async_copy",
        ),
    ),
    SyntheticTrajectory(
        task_name="jobshop_abz",
        baseline=SCHED_BASELINE,
        candidate=SCHED_CANDIDATE,
        target_kinds=(
            "reorder_operations",
            "add_priority_heuristic",
            "add_dispatch_rule",
            "add_dependency_constraint",
        ),
    ),
)


__all__ = (
    "AES_BASELINE", "AES_CANDIDATE",
    "CUDA_BASELINE", "CUDA_CANDIDATE",
    "SCHED_BASELINE", "SCHED_CANDIDATE",
    "SyntheticTrajectory", "SYNTHETIC_TRAJECTORIES",
)
