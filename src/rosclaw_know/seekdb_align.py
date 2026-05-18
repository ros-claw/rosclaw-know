"""SeekDB entity-alignment + dedup (READ-ONLY).

Know never writes to SeekDB. If SeekDB is unreachable or its collections are
empty (typical for a fresh install), we silently fall back to ``create_new``
so the pipeline keeps moving.

How (the sister project) is the only writer; Know just looks up.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from .config import SEEKDB_AVAILABLE

log = logging.getLogger("rosclaw_know.seekdb_align")

# Singletons — lazily initialised once
_client = None
_client_init_lock = threading.Lock()
_client_init_attempted = False

_embed_model = None
_embed_lock = threading.Lock()


def _get_client():
    """Lazy-init SeekDB client. Returns None if unavailable."""
    global _client, _client_init_attempted
    if _client_init_attempted:
        return _client
    with _client_init_lock:
        if _client_init_attempted:
            return _client
        _client_init_attempted = True
        if not SEEKDB_AVAILABLE:
            log.info("SeekDB not configured — entity alignment will be skipped.")
            return None
        try:
            from seekdb_collection_client import SeekDBCollectionClient  # type: ignore
            _client = SeekDBCollectionClient()
            log.info("SeekDB client ready (read-only).")
        except Exception as exc:  # noqa: BLE001
            log.info("SeekDB client unavailable (%s) — entity alignment skipped.", exc)
            _client = None
    return _client


def _get_embed_model():
    """Lazy-init sentence-transformer. ~120 MB download on first call."""
    global _embed_model
    if _embed_model is not None:
        return _embed_model
    with _embed_lock:
        if _embed_model is not None:
            return _embed_model
        try:
            from sentence_transformers import SentenceTransformer

            from .config import EMBEDDING_MODEL
            _embed_model = SentenceTransformer(EMBEDDING_MODEL)
        except Exception as exc:  # noqa: BLE001
            log.warning("sentence-transformers init failed: %s", exc)
            _embed_model = None
    return _embed_model


def check_duplicate_and_align(
    symptom_text: str,
    domain: str,
    fix_pattern: str,
    *,
    source_url: str | None = None,
    arxiv_id: str | None = None,
    similarity_threshold: float = 0.88,
) -> dict[str, Any]:
    """Decide what to do with a freshly extracted heuristic.

    Returns one of:
        {"action": "skip",       "reason": "..."}
        {"action": "merge",      "existing_id": "...", "similarity": float, "new_fix": str}
        {"action": "create_new"}
    """
    client = _get_client()
    if client is None:
        return {"action": "create_new"}

    try:
        # Dedup by external identifier
        if source_url or arxiv_id:
            pages_coll = client.get_or_create_collection("wiki_pages")
            if arxiv_id:
                existing = pages_coll.query(where_metadata={"arxiv_id": arxiv_id})
                if existing:
                    return {"action": "skip", "reason": f"arxiv_id {arxiv_id} exists"}
            if source_url:
                existing = pages_coll.query(where_metadata={"source_url": source_url})
                if existing:
                    return {"action": "skip", "reason": f"source_url {source_url} exists"}
    except Exception as exc:  # noqa: BLE001
        log.debug("dedup query failed (%s) — falling through", exc)

    try:
        symptom_coll = client.get_or_create_collection("symptom_index")
        model = _get_embed_model()
        if model is None:
            return {"action": "create_new"}
        query_vec = model.encode([symptom_text])[0].tolist()
        results = symptom_coll.query(query_embeddings=[query_vec], n_results=5)
        if results and results.get("ids") and results["ids"][0]:
            distance = results["distances"][0][0]
            similarity = max(0.0, 1.0 - float(distance))
            if similarity > similarity_threshold:
                return {
                    "action": "merge",
                    "existing_id": results["ids"][0][0],
                    "similarity": similarity,
                    "new_fix": fix_pattern,
                }
    except Exception as exc:  # noqa: BLE001
        log.debug("symptom_index probe failed (%s)", exc)

    return {"action": "create_new"}
