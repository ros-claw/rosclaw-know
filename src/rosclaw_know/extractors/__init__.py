"""Sprint 2+ extractors — turn raw benchmark / repo / paper / trajectory
inputs into typed knowledge objects (TaskCard, FailureMode, FixPattern…).

Each extractor is deterministic where it can be — LLM only when the
source genuinely cannot be parsed structurally.  See the v1.5 plan §5.
"""
from .benchmark_extractor import (
    ExtractInput,
    extract_from_corpus,
    extract_task_card,
    load_task_dir,
)
from .code_diff_summarizer import DiffSummary, summarize_diff
from .trajectory_extractor import (
    ALL_FEATURE_EXTRACTORS,
    extract_candidate_patterns,
    extract_optimizer_features,
    extract_pid_features,
    extract_systems_features,
    from_baseline_archive_pair,
    from_iteration_dir,
)

__all__ = [
    "ExtractInput",
    "extract_from_corpus",
    "extract_task_card",
    "load_task_dir",
    "DiffSummary",
    "summarize_diff",
    "ALL_FEATURE_EXTRACTORS",
    "extract_candidate_patterns",
    "extract_optimizer_features",
    "extract_pid_features",
    "extract_systems_features",
    "from_baseline_archive_pair",
    "from_iteration_dir",
]
