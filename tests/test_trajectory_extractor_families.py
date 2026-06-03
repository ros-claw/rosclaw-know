"""Sprint 3 收尾: tests for AES / CUDA / scheduling feature extractors."""
from __future__ import annotations

from rosclaw_know.extractors._sprint3_synthetic import (
    AES_BASELINE,
    AES_CANDIDATE,
    CUDA_BASELINE,
    CUDA_CANDIDATE,
    SCHED_BASELINE,
    SCHED_CANDIDATE,
)
from rosclaw_know.extractors.code_diff_summarizer import summarize_diff
from rosclaw_know.extractors.trajectory_extractor import (
    extract_aes_features,
    extract_cuda_features,
    extract_scheduling_features,
    from_baseline_archive_pair,
)

# ── AES detectors ───────────────────────────────────────────────────────


def test_aes_lookup_table_detected() -> None:
    summary = summarize_diff(AES_BASELINE, AES_CANDIDATE)
    kinds = {m.kind for m in summary.mutations}
    assert "add_lookup_table" in kinds


def test_aes_unroll_pragma_detected() -> None:
    summary = summarize_diff(AES_BASELINE, AES_CANDIDATE)
    kinds = {m.kind for m in summary.mutations}
    assert "unroll_loop" in kinds


def test_aes_branchless_select_detected() -> None:
    summary = summarize_diff(AES_BASELINE, AES_CANDIDATE)
    kinds = {m.kind for m in summary.mutations}
    assert "add_branchless_select" in kinds


def test_aes_constant_time_compare_detected() -> None:
    summary = summarize_diff(AES_BASELINE, AES_CANDIDATE)
    kinds = {m.kind for m in summary.mutations}
    assert "add_constant_time_compare" in kinds


def test_aes_detectors_dont_leak_sbox_bytes() -> None:
    """Plan §3.5: descriptions must not contain raw S-box byte values."""
    summary = summarize_diff(AES_BASELINE, AES_CANDIDATE)
    for m in summary.mutations:
        # We allow hex tokens inside identifier names (e.g. ``TE0``) but
        # the description text itself must not embed concrete bytes.
        assert "0x63" not in m.description
        assert "0x7c" not in m.description


def test_aes_extractor_emits_four_candidates() -> None:
    """Plan §11.4: AES extractor produces table / unroll / branchless / const-time."""
    traj = from_baseline_archive_pair(
        baseline_text=AES_BASELINE,
        candidate_text=AES_CANDIDATE,
        task_name="AES-128",
        trajectory_id="t_aes_synth",
    )
    cands = extract_aes_features(traj)
    ids = {c.id for c in cands}
    assert "candidate_aes_use_precomputed_tables" in ids
    assert "candidate_aes_unroll_round_structure" in ids
    assert "candidate_aes_branchless_select" in ids
    assert "candidate_aes_constant_time_compare" in ids
    assert len(ids) == 4


def test_aes_extractor_skips_non_aes_trajectory() -> None:
    traj = from_baseline_archive_pair(
        baseline_text="x = 1", candidate_text="x = 2",
        task_name="optics_tuning", trajectory_id="t_optics",
    )
    assert extract_aes_features(traj) == []


# ── CUDA detectors ──────────────────────────────────────────────────────


def test_cuda_shared_memory_tile_detected() -> None:
    summary = summarize_diff(CUDA_BASELINE, CUDA_CANDIDATE)
    kinds = {m.kind for m in summary.mutations}
    assert "add_shared_memory_tile" in kinds


def test_cuda_adjust_block_size_detected() -> None:
    summary = summarize_diff(CUDA_BASELINE, CUDA_CANDIDATE)
    kinds = {m.kind for m in summary.mutations}
    assert "adjust_block_size" in kinds


def test_cuda_warp_specialization_detected() -> None:
    summary = summarize_diff(CUDA_BASELINE, CUDA_CANDIDATE)
    kinds = {m.kind for m in summary.mutations}
    assert "add_warp_specialization" in kinds


def test_cuda_async_copy_detected() -> None:
    summary = summarize_diff(CUDA_BASELINE, CUDA_CANDIDATE)
    kinds = {m.kind for m in summary.mutations}
    assert "add_async_copy" in kinds


def test_cuda_extractor_emits_four_candidates() -> None:
    """Plan §11.7: flash_attention pack 能召回 CUDA memory/tiling patterns."""
    traj = from_baseline_archive_pair(
        baseline_text=CUDA_BASELINE,
        candidate_text=CUDA_CANDIDATE,
        task_name="FlashAttention",
        trajectory_id="t_cuda_synth",
    )
    cands = extract_cuda_features(traj)
    ids = {c.id for c in cands}
    assert "candidate_cuda_shared_memory_tiling" in ids
    assert "candidate_cuda_tune_block_size" in ids
    assert "candidate_cuda_warp_specialization" in ids
    assert "candidate_cuda_async_global_to_shared_copy" in ids


def test_cuda_extractor_skips_non_cuda_task() -> None:
    traj = from_baseline_archive_pair(
        baseline_text="x = 1", candidate_text="x = 2",
        task_name="pid_tuning", trajectory_id="t_pid",
    )
    assert extract_cuda_features(traj) == []


# ── Scheduling detectors ────────────────────────────────────────────────


def test_sched_reorder_operations_detected() -> None:
    summary = summarize_diff(SCHED_BASELINE, SCHED_CANDIDATE)
    kinds = {m.kind for m in summary.mutations}
    assert "reorder_operations" in kinds


def test_sched_priority_heuristic_detected() -> None:
    summary = summarize_diff(SCHED_BASELINE, SCHED_CANDIDATE)
    kinds = {m.kind for m in summary.mutations}
    assert "add_priority_heuristic" in kinds


def test_sched_dispatch_rule_detected() -> None:
    summary = summarize_diff(SCHED_BASELINE, SCHED_CANDIDATE)
    kinds = {m.kind for m in summary.mutations}
    assert "add_dispatch_rule" in kinds


def test_sched_dependency_constraint_detected() -> None:
    summary = summarize_diff(SCHED_BASELINE, SCHED_CANDIDATE)
    kinds = {m.kind for m in summary.mutations}
    assert "add_dependency_constraint" in kinds


def test_sched_extractor_emits_four_candidates() -> None:
    traj = from_baseline_archive_pair(
        baseline_text=SCHED_BASELINE,
        candidate_text=SCHED_CANDIDATE,
        task_name="jobshop_abz",
        trajectory_id="t_sched_synth",
    )
    cands = extract_scheduling_features(traj)
    ids = {c.id for c in cands}
    assert "candidate_sched_explicit_operation_ordering" in ids
    assert "candidate_sched_priority_heuristic" in ids
    assert "candidate_sched_named_dispatch_rule" in ids
    assert "candidate_sched_explicit_dependency_constraints" in ids


def test_sched_extractor_fires_on_data_center_task() -> None:
    traj = from_baseline_archive_pair(
        baseline_text=SCHED_BASELINE,
        candidate_text=SCHED_CANDIDATE,
        task_name="SustainableDataCenterControl",
        trajectory_id="t_dc",
    )
    cands = extract_scheduling_features(traj)
    assert len(cands) >= 1


# ── Plan §11.4 invariants ───────────────────────────────────────────────


def test_no_concrete_byte_values_leak_in_aes_descriptions() -> None:
    """No specific S-box byte should appear in any mutation description."""
    summary = summarize_diff(AES_BASELINE, AES_CANDIDATE)
    forbidden = ["0x63", "0x7c", "0x77", "0xf3"]
    for m in summary.mutations:
        for byte in forbidden:
            assert byte not in m.description


def test_no_concrete_block_size_value_leaks_in_cuda_descriptions() -> None:
    """Plan §3.5: tuned block-size value must not appear in the description."""
    summary = summarize_diff(CUDA_BASELINE, CUDA_CANDIDATE)
    for m in summary.mutations:
        if m.kind == "adjust_block_size":
            assert "64" not in m.description, m.description
            assert "32" not in m.description, m.description
