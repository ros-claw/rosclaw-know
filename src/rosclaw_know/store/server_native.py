"""Native SeekDB server SQL path for full-text/vector/RRF hybrid retrieval."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from rosclaw_know.contracts.base import StrictContract

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_FILTER_FIELDS = {"source_authority", "compatibility_status", "status", "unit_type"}


class NativeHybridDocument(StrictContract):
    record_id: str
    content: str
    zh_content: str = ""
    error_surface: str = ""
    symbol_surface: str = ""
    path_surface: str = ""
    api_surface: str = ""
    source_authority: Literal["S", "A", "B", "C", "D"] = "D"
    compatibility_status: Literal[
        "compatible", "partially_compatible", "incompatible", "unknown"
    ] = "unknown"
    status: str = "active"
    unit_type: str = "implementation"
    embedding: list[float] = Field(min_length=1)


class NativeHybridTrace(StrictContract):
    query: str
    profile: Literal[
        "PROFILE_ERROR", "PROFILE_CODE", "PROFILE_CONCEPT", "PROFILE_PROJECT"
    ]
    table: str
    generated_sql: str
    results: list[dict[str, Any]]
    latency_ms: float = Field(ge=0.0)
    rank: Literal["rrf"] = "rrf"
    relational_filters: dict[str, str] = Field(default_factory=dict)


class NativeHybridQueryEngine:
    """A small SQL adapter; callers own the PEP-249 connection lifecycle."""

    def __init__(self, connection: Any, *, table: str, dimension: int) -> None:
        if not _IDENTIFIER.fullmatch(table):
            raise ValueError(f"unsafe table identifier: {table!r}")
        if dimension <= 0 or dimension > 65_535:
            raise ValueError("vector dimension must be between 1 and 65535")
        self.connection = connection
        self.table = table
        self.dimension = dimension

    def ensure_schema(self) -> None:
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.table} (
            record_id VARCHAR(240),
            content TEXT,
            zh_content TEXT,
            error_surface VARCHAR(2048),
            symbol_surface VARCHAR(2048),
            path_surface VARCHAR(4096),
            api_surface VARCHAR(2048),
            source_authority CHAR(1),
            compatibility_status VARCHAR(32),
            status VARCHAR(32),
            unit_type VARCHAR(64),
            embedding VECTOR({self.dimension}),
            FULLTEXT INDEX ft_content(content) WITH PARSER BENG,
            FULLTEXT INDEX ft_zh(zh_content) WITH PARSER IK,
            FULLTEXT INDEX ft_error(error_surface) WITH PARSER NGRAM
                PARSER_PROPERTIES=(ngram_token_size=2),
            FULLTEXT INDEX ft_symbol(symbol_surface) WITH PARSER NGRAM
                PARSER_PROPERTIES=(ngram_token_size=2),
            FULLTEXT INDEX ft_path(path_surface) WITH PARSER NGRAM
                PARSER_PROPERTIES=(ngram_token_size=2),
            FULLTEXT INDEX ft_api(api_surface) WITH PARSER NGRAM
                PARSER_PROPERTIES=(ngram_token_size=2),
            VECTOR INDEX vec_embedding(embedding)
                WITH (distance=cosine, type=hnsw, lib=vsag)
        ) ORGANIZATION HEAP
        """
        with self.connection.cursor() as cursor:
            cursor.execute(sql)
        self.connection.commit()

    def put(self, document: NativeHybridDocument) -> None:
        if len(document.embedding) != self.dimension:
            raise ValueError(
                f"embedding dimension mismatch: expected {self.dimension}, "
                f"got {len(document.embedding)}"
            )
        values = (
            document.record_id,
            document.content,
            document.zh_content,
            document.error_surface,
            document.symbol_surface,
            document.path_surface,
            document.api_surface,
            document.source_authority,
            document.compatibility_status,
            document.status,
            document.unit_type,
            json.dumps(document.embedding, separators=(",", ":")),
        )
        try:
            self.connection.begin()
            with self.connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self.table} WHERE record_id = %s", (document.record_id,))
                cursor.execute(
                    f"INSERT INTO {self.table} VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    values,
                )
            self.connection.commit()
            self.refresh_vector_index()
            self._wait_until_vector_visible(document.record_id, document.embedding)
        except Exception:
            self.connection.rollback()
            raise

    def refresh_vector_index(self) -> None:
        """Make incremental vector writes immediately visible to ANN queries.

        SeekDB keeps recent HNSW writes in an incremental structure.  Explicitly
        refreshing it gives the online Know path read-after-write semantics,
        which matters more here than maximizing bulk-ingest throughput.
        """

        with self.connection.cursor() as cursor:
            cursor.execute(
                "CALL DBMS_VECTOR.REFRESH_INDEX(%s, %s, %s, %s, %s)",
                ("vec_embedding", self.table, "embedding", 1, "FAST"),
            )
        self.connection.commit()

    def rebuild_vector_index(self) -> None:
        """Force a full HNSW rebuild after bulk deletion/restoration."""

        with self.connection.cursor() as cursor:
            cursor.execute(
                "CALL DBMS_VECTOR.REBUILD_INDEX(%s, %s, %s, %s)",
                ("vec_embedding", self.table, "embedding", 0),
            )
        self.connection.commit()

    def _wait_until_vector_visible(
        self, record_id: str, embedding: list[float], *, timeout_seconds: float = 2.0
    ) -> None:
        """Bound the asynchronous HNSW refresh before exposing a successful write."""

        deadline = time.monotonic() + timeout_seconds
        encoded = json.dumps(embedding, separators=(",", ":"))
        while time.monotonic() < deadline:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT record_id FROM {self.table} "
                    "ORDER BY cosine_distance(embedding, %s) APPROXIMATE LIMIT 1000",
                    (encoded,),
                )
                if record_id in {str(row[0]) for row in cursor.fetchall()}:
                    return
            time.sleep(0.02)
        raise RuntimeError(
            f"SeekDB vector index did not expose {record_id!r} within "
            f"{timeout_seconds:.1f}s"
        )

    @staticmethod
    def _fields(profile: str) -> list[str]:
        return {
            "PROFILE_ERROR": [
                "error_surface^4",
                "symbol_surface^2",
                "api_surface^2",
                "path_surface^1.5",
                "content",
            ],
            "PROFILE_CODE": [
                "symbol_surface^4",
                "path_surface^3",
                "api_surface^2",
                "content",
            ],
            "PROFILE_CONCEPT": ["content^3", "zh_content^2"],
            "PROFILE_PROJECT": ["content^3", "path_surface", "api_surface"],
        }[profile]

    def query(
        self,
        *,
        query: str,
        embedding: list[float],
        profile: Literal[
            "PROFILE_ERROR", "PROFILE_CODE", "PROFILE_CONCEPT", "PROFILE_PROJECT"
        ],
        filters: dict[str, str] | None = None,
        limit: int = 10,
        rank_window_size: int | None = None,
        rank_constant: int = 60,
    ) -> NativeHybridTrace:
        if len(embedding) != self.dimension:
            raise ValueError("query embedding dimension does not match the native index")
        if limit <= 0 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        filters = dict(filters or {})
        unknown = set(filters) - _FILTER_FIELDS
        if unknown:
            raise ValueError(f"unsupported relational filters: {sorted(unknown)}")
        filter_terms = [{"term": {key: value}} for key, value in sorted(filters.items())]
        text_query: dict[str, Any] = {
            "query_string": {
                "fields": self._fields(profile),
                "query": query,
                "type": "best_fields",
            }
        }
        query_clause: dict[str, Any] = text_query
        if filter_terms:
            query_clause = {"bool": {"must": [text_query], "filter": filter_terms}}
        knn: dict[str, Any] = {
            "field": "embedding",
            "k": max(limit, 5),
            "query_vector": embedding,
        }
        if filter_terms:
            knn["filter"] = filter_terms
        window = rank_window_size or max(limit * 4, 20)
        payload = {
            "query": query_clause,
            "knn": knn,
            "rank": {"rrf": {"rank_window_size": window, "rank_constant": rank_constant}},
            "_source": [
                "record_id",
                "content",
                "source_authority",
                "compatibility_status",
                "status",
                "unit_type",
                "_keyword_score",
                "_semantic_score",
            ],
            "size": limit,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        started = time.perf_counter()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT DBMS_HYBRID_SEARCH.SEARCH(%s, %s)", (self.table, encoded)
            )
            raw = cursor.fetchone()[0]
            cursor.execute(
                "SELECT DBMS_HYBRID_SEARCH.GET_SQL(%s, %s)", (self.table, encoded)
            )
            generated_sql = str(cursor.fetchone()[0])
        latency_ms = (time.perf_counter() - started) * 1000
        results = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(results, list):
            raise RuntimeError("SeekDB hybrid search returned a non-list result")
        return NativeHybridTrace(
            query=query,
            profile=profile,
            table=self.table,
            generated_sql=generated_sql,
            results=results,
            latency_ms=latency_ms,
            relational_filters=filters,
        )

    def fulltext(self, *, field: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if field not in {
            "content",
            "zh_content",
            "error_surface",
            "symbol_surface",
            "path_surface",
            "api_surface",
        }:
            raise ValueError(f"unsupported fulltext field: {field}")
        sql = (
            f"SELECT record_id, MATCH({field}) AGAINST(%s IN NATURAL LANGUAGE MODE) score "
            f"FROM {self.table} WHERE MATCH({field}) AGAINST(%s IN NATURAL LANGUAGE MODE) "
            "ORDER BY score DESC LIMIT %s"
        )
        with self.connection.cursor() as cursor:
            cursor.execute(sql, (query, query, limit))
            return [
                {"record_id": row[0], "score": float(row[1])}
                for row in cursor.fetchall()
            ]

    def vector(self, *, embedding: list[float], limit: int = 10) -> list[dict[str, Any]]:
        if len(embedding) != self.dimension:
            raise ValueError("query embedding dimension does not match the native index")
        encoded = json.dumps(embedding, separators=(",", ":"))
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"SELECT record_id, cosine_distance(embedding, %s) distance "
                f"FROM {self.table} ORDER BY cosine_distance(embedding, %s) "
                "APPROXIMATE LIMIT %s",
                (encoded, encoded, limit),
            )
            return [
                {"record_id": row[0], "distance": float(row[1])}
                for row in cursor.fetchall()
            ]

    def rerank_capability(self, model_key: str | None = None) -> dict[str, Any]:
        if not model_key:
            return {
                "available": False,
                "reason": "AI_RERANK model key is not configured",
                "fallback": "deterministic_rrf",
            }
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT AI_RERANK(%s, %s, JSON_ARRAY(%s, %s))",
                    (model_key, "capability probe", "document one", "document two"),
                )
                cursor.fetchone()
            return {"available": True, "model_key": model_key, "fallback": None}
        except Exception as exc:  # noqa: BLE001 - capability probe
            return {
                "available": False,
                "reason": f"{type(exc).__name__}: {str(exc)[:300]}",
                "fallback": "deterministic_rrf",
            }

    def logical_backup(self, path: str | Path) -> dict[str, Any]:
        target = Path(path).expanduser().resolve(strict=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"SELECT record_id, content, zh_content, error_surface, symbol_surface, "
                f"path_surface, api_surface, source_authority, compatibility_status, "
                f"status, unit_type, embedding FROM {self.table} ORDER BY record_id"
            )
            rows = cursor.fetchall()
        payload = {
            "schema_version": "rosclaw.know.native_hybrid_backup.v1",
            "table": self.table,
            "dimension": self.dimension,
            "records": [list(row) for row in rows],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str)
        target.write_text(encoded, encoding="utf-8")
        return {
            "path": str(target),
            "records": len(rows),
            "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        }

    def restore_logical_backup(self, path: str | Path) -> dict[str, Any]:
        source = Path(path).expanduser().resolve(strict=True)
        payload = json.loads(source.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "rosclaw.know.native_hybrid_backup.v1":
            raise ValueError("unsupported native hybrid backup schema")
        if int(payload.get("dimension", 0)) != self.dimension:
            raise ValueError("backup embedding dimension does not match the target table")
        restored = 0
        for row in payload.get("records") or []:
            embedding = json.loads(row[11]) if isinstance(row[11], str) else row[11]
            self.put(
                NativeHybridDocument(
                    record_id=row[0],
                    content=row[1],
                    zh_content=row[2],
                    error_surface=row[3],
                    symbol_surface=row[4],
                    path_surface=row[5],
                    api_surface=row[6],
                    source_authority=row[7],
                    compatibility_status=row[8],
                    status=row[9],
                    unit_type=row[10],
                    embedding=embedding,
                )
            )
            restored += 1
        if restored:
            self.rebuild_vector_index()
            first = payload["records"][0]
            first_embedding = (
                json.loads(first[11]) if isinstance(first[11], str) else first[11]
            )
            self._wait_until_vector_visible(first[0], first_embedding)
        return {"path": str(source), "records": restored}


__all__ = ["NativeHybridDocument", "NativeHybridQueryEngine", "NativeHybridTrace"]
