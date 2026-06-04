"""Async research worker — turns an agent's research request into bridge clusters.

Pipeline per job:

  1. Resolve sources for the topic
        a. arXiv search → top-N abstracts
        b. GitHub search → top-N awesome lists / repo READMEs
        c. (optional) web search → top-N pages
  2. Download each source's text content (best-effort).
  3. Run the standard rosclaw-know pipeline:
        harvester → weaver → incremental Muse → bridge merge
  4. Optionally notify rosclaw-how to /admin/reload so the new clusters
     are immediately routable.
  5. Update the ResearchStore job entry with summary stats.

Each job runs in a single asyncio Task. We deliberately serialize jobs
(one at a time per process) so the Muse LLM concurrency budget is shared
correctly across the pipeline — fan-out would just thrash the rate limit.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config
from .incremental_pipeline import run_incremental_ingest
from .research_sources import collect_sources
from .research_store import ResearchJob, ResearchStore

logger = logging.getLogger("rosclaw_know.research_worker")

# Where the worker drops fetched markdown for the harvester to pick up.
# Historically this lived under wiki/research_corpus/<job_id>/, but
# WIKI_DIR is symlinked to a sibling repo that may be missing or broken
# in fresh clones — and mkdir(parents=True) crashes with EEXIST when it
# walks into a broken symlink.  research_corpus is *derived* state
# (LLM-fetched documents we'll ingest), so it belongs under data/ where
# the rest of the pipeline output lives, not under the input-only
# wiki tree.  Override with ROSCLAW_KNOW_RESEARCH_CORPUS_DIR if you
# need to keep the legacy layout.
_RESEARCH_CORPUS_ROOT = Path(
    os.environ.get(
        "ROSCLAW_KNOW_RESEARCH_CORPUS_DIR",
        str(config.DATA_DIR / "research_corpus"),
    )
)

# Hard wall-clock cap on collect_sources (sum of three channel timeouts is
# ~30s under normal conditions; this is the defensive ceiling).
_COLLECT_TIMEOUT = 90.0

# Cap on the how-reload notification. The actual /admin/reload is fast for a
# delta (~500 ms no-change, ~1.5 s per new cluster); cap at 120 s so a hung
# rosclaw-how doesn't block the worker indefinitely.
_RELOAD_TIMEOUT = 120.0


class ResearchWorker:
    """One worker per process; serialises jobs through a single queue."""

    def __init__(self, store: ResearchStore, *, how_reload_url: str | None = None,
                 how_api_key: str | None = None) -> None:
        self.store = store
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        # Auto-reload hook — when set, finished jobs POST to rosclaw-how.
        self.how_reload_url = how_reload_url or os.environ.get(
            "ROSCLAW_HOW_RELOAD_URL", "http://127.0.0.1:47820/wiki/v1/admin/reload"
        )
        self.how_api_key = how_api_key or os.environ.get(
            "ROSCLAW_HOW_API_KEY", "rw_sk_dev_local"
        )

    # ── public API ───────────────────────────────────────────────────────

    def submit(self, job: ResearchJob) -> None:
        """Schedule a job. The caller already wrote it to the store."""
        self.queue.put_nowait(job.job_id)
        logger.info("Job %s queued: topic=%r depth=%s", job.job_id, job.topic, job.depth)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop(), name="research-worker")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── private ──────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        logger.info("Research worker loop online")
        while True:
            try:
                job_id = await self.queue.get()
            except asyncio.CancelledError:
                logger.info("Worker stopping")
                return
            try:
                await self._run_job(job_id)
            except Exception as exc:  # noqa: BLE001 — never let one job kill the worker
                logger.exception("Job %s crashed: %s", job_id, exc)
                self.store.update(job_id, status="failed", error=str(exc))
            finally:
                self.queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            logger.warning("Job %s vanished from store", job_id)
            return
        self.store.update(job_id, status="running")
        t0 = time.perf_counter()

        # 1) Resolve + fetch sources (wall-clock capped — defense-in-depth on
        #    top of the per-channel timeouts in research_sources.py).
        try:
            sources = await asyncio.wait_for(
                collect_sources(
                    job.topic, depth=job.depth, budget_tokens=job.budget_tokens
                ),
                timeout=_COLLECT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            err = f"collect_sources timed out after {_COLLECT_TIMEOUT}s"
            logger.warning("Job %s: %s", job_id, err)
            self.store.update(job_id, status="failed", error=err)
            return
        self.store.update(
            job_id,
            sources_planned=len(sources),
        )

        # Short-circuit: zero sources fetched means all three channels
        # (arXiv / GitHub / Brave) returned nothing usable. There is no point
        # running the incremental pipeline on an empty directory — Muse would
        # add zero clusters and we'd still pay the LLM-config check + a
        # how-reload round-trip. Mark completed with a clear summary so the
        # warmup driver can move on instead of polling forever.
        if not sources:
            elapsed = time.perf_counter() - t0
            self.store.update(
                job_id,
                status="completed",
                clusters_added=0,
                new_cluster_ids=[],
                summary=(
                    f"No sources fetched for {job.topic!r} (arXiv/GitHub/web "
                    f"all returned 0). Elapsed {elapsed:.1f}s. Check network "
                    "or topic phrasing."
                ),
            )
            logger.info("Job %s: 0 sources — short-circuit to completed", job_id)
            return

        # 2) Drop fetched markdowns into wiki/research_corpus/<job_id>/
        out_dir = _RESEARCH_CORPUS_ROOT / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        sources_fetched = 0
        for src in sources:
            fp = out_dir / src.filename
            fp.write_text(src.markdown_body, encoding="utf-8")
            sources_fetched += 1
        self.store.update(job_id, sources_fetched=sources_fetched)

        # 3) Run incremental pipeline on the freshly-dropped files
        before = self._cluster_count()
        try:
            summary = await run_incremental_ingest([out_dir])
        except RuntimeError as exc:
            self.store.update(job_id, status="failed", error=str(exc))
            return
        after = self._cluster_count()
        added = summary.get("muse", {}).get("new_clusters_minted", 0) if isinstance(summary, dict) else 0
        new_cluster_ids = self._new_cluster_ids_since(before, after)

        # 4) Auto-reload rosclaw-how (best-effort, non-blocking failure)
        reload_status = await self._notify_how_reload()

        elapsed = time.perf_counter() - t0
        summary_text = (
            f"Researched {job.topic!r} in {elapsed:.1f}s. "
            f"Fetched {sources_fetched}/{len(sources)} sources, "
            f"minted {added} new clusters. how-reload={reload_status}."
        )
        self.store.update(
            job_id,
            status="completed",
            clusters_added=added,
            new_cluster_ids=new_cluster_ids,
            summary=summary_text,
        )
        logger.info("Job %s done. %s", job_id, summary_text)

    def _cluster_count(self) -> int:
        try:
            import json
            data = json.loads((config.ASSETS_DIR / "bridge_index.json").read_text(encoding="utf-8"))
            return len(data.get("symptom_clusters", {}))
        except Exception:  # noqa: BLE001 — counting is diagnostic only
            return 0

    def _new_cluster_ids_since(self, before: int, after: int) -> list[str]:
        if after <= before:
            return []
        try:
            import json
            data = json.loads((config.ASSETS_DIR / "bridge_index.json").read_text(encoding="utf-8"))
            ids = list(data.get("symptom_clusters", {}).keys())
            return ids[-(after - before):]
        except Exception:  # noqa: BLE001
            return []

    async def _notify_how_reload(self) -> str:
        if not self.how_reload_url:
            return "skipped"
        try:
            req = urllib.request.Request(
                self.how_reload_url,
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": self.how_api_key or "",
                },
                method="POST",
            )
            # urlopen is blocking; run in default executor to keep the
            # asyncio loop responsive. Bounded by _RELOAD_TIMEOUT so a hung
            # rosclaw-how can't park the research worker forever.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=_RELOAD_TIMEOUT).read(),
            )
            return "ok"
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            logger.warning("rosclaw-how reload notification failed: %s", exc)
            return f"failed({exc})"


__all__ = ["ResearchWorker"]
