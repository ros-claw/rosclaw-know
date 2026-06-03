"""Sprint 3 收尾 synthetic-trajectory corpus (script-level re-export).

The actual fixture content lives in
:mod:`rosclaw_know.extractors._sprint3_synthetic` so tests and other
in-package callers can import it without putting ``scripts/`` on
``sys.path``.  This file is the thin shim that
``scripts/extract_trajectory_patterns.py`` still imports as
``scripts._sprint3_synthetic_corpus``.
"""
from __future__ import annotations

from rosclaw_know.extractors._sprint3_synthetic import (
    SYNTHETIC_TRAJECTORIES,
    SyntheticTrajectory,
)

__all__ = ("SYNTHETIC_TRAJECTORIES", "SyntheticTrajectory")
