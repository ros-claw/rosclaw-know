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

__all__ = [
    "ExtractInput",
    "extract_from_corpus",
    "extract_task_card",
    "load_task_dir",
]
