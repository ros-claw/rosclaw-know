"""Tests for the research worker's hung-job defenses (know#1)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from rosclaw_know import research_worker
from rosclaw_know.research_store import ResearchStore


@pytest.fixture
def store(tmp_path: Path) -> ResearchStore:
    return ResearchStore.load(tmp_path / "store.jsonl")


@pytest.fixture
def worker(store: ResearchStore) -> research_worker.ResearchWorker:
    # how_reload_url=None to keep _notify_how_reload from making real HTTP
    # calls; the tests below only exercise the source-fetch branch.
    return research_worker.ResearchWorker(store, how_reload_url=None)


@pytest.mark.asyncio
async def test_zero_sources_short_circuits_to_completed(
    monkeypatch: pytest.MonkeyPatch,
    store: ResearchStore,
    worker: research_worker.ResearchWorker,
) -> None:
    """When all source channels return zero results, the job must reach
    `completed` (with clusters_added=0) instead of running the incremental
    pipeline + reload + hanging. Closes know#1."""

    async def fake_collect(*_args: Any, **_kwargs: Any) -> list:
        return []

    monkeypatch.setattr(research_worker, "collect_sources", fake_collect)

    # The pipeline must NOT be called when there are 0 sources.
    pipeline_called = {"hit": False}

    async def boom_pipeline(*_args: Any, **_kwargs: Any) -> dict:
        pipeline_called["hit"] = True
        raise AssertionError("incremental pipeline should not run on 0 sources")

    monkeypatch.setattr(research_worker, "run_incremental_ingest", boom_pipeline)

    job = store.create(topic="unreachable topic", depth="shallow", budget_tokens=50_000)
    await worker._run_job(job.job_id)

    after = store.get(job.job_id)
    assert after is not None
    assert after.status == "completed", after.status
    assert after.clusters_added == 0
    assert "No sources fetched" in (after.summary or "")
    assert pipeline_called["hit"] is False


@pytest.mark.asyncio
async def test_collect_timeout_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
    store: ResearchStore,
    worker: research_worker.ResearchWorker,
) -> None:
    """If collect_sources itself hangs beyond _COLLECT_TIMEOUT, the wall-
    clock cap must trip and mark the job failed (not leave it `running`
    forever). Closes know#1."""

    # Force a faster ceiling so the test doesn't actually wait 90 s.
    monkeypatch.setattr(research_worker, "_COLLECT_TIMEOUT", 0.2)

    async def hang_collect(*_args: Any, **_kwargs: Any) -> list:
        await asyncio.sleep(5.0)  # > 0.2s — must be cancelled
        return []

    monkeypatch.setattr(research_worker, "collect_sources", hang_collect)

    job = store.create(topic="will hang", depth="shallow", budget_tokens=50_000)
    await worker._run_job(job.job_id)

    after = store.get(job.job_id)
    assert after is not None
    assert after.status == "failed"
    assert "timed out" in (after.error or "")
