"""Opt-in final-acceptance tests against a real SeekDB server.

Run with ``ROSCLAW_KNOW_LIVE_SEEKDB=1 pytest -q tests/live_knowledge``.
The database and table named here are acceptance-only resources.
"""

from __future__ import annotations

import os

import pytest

from rosclaw_know.store.server_native import NativeHybridDocument, NativeHybridQueryEngine

pytestmark = pytest.mark.skipif(
    os.environ.get("ROSCLAW_RUN_LIVE_KNOWLEDGE") != "1"
    and os.environ.get("ROSCLAW_KNOW_LIVE_SEEKDB") != "1",
    reason="set ROSCLAW_RUN_LIVE_KNOWLEDGE=1 to run live SeekDB acceptance",
)


@pytest.fixture
def engine():
    pymysql = pytest.importorskip("pymysql")
    connection = pymysql.connect(
        host=os.environ.get("ROSCLAW_KNOW_SEEKDB_HOST", "127.0.0.1"),
        port=int(os.environ.get("ROSCLAW_KNOW_SEEKDB_PORT", "2881")),
        user=os.environ.get("ROSCLAW_KNOW_SEEKDB_USER", "root"),
        password=os.environ.get("ROSCLAW_KNOW_SEEKDB_PASSWORD", ""),
        database=os.environ.get(
            "ROSCLAW_KNOW_SEEKDB_DATABASE", "rosclaw_know_acceptance"
        ),
        autocommit=False,
    )
    adapter = NativeHybridQueryEngine(
        connection, table="know_native_live_test", dimension=4
    )
    adapter.ensure_schema()
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM know_native_live_test")
    connection.commit()
    yield adapter
    connection.close()


def _seed(engine: NativeHybridQueryEngine) -> None:
    engine.put(
        NativeHybridDocument(
            record_id="current",
            content="RealSense camera timeout recovery and USB permissions",
            zh_content="相机超时恢复",
            error_surface="RuntimeError timeout -12",
            symbol_surface="RealSensePipeline wait_for_frames",
            path_surface="src/realsense/camera.py",
            api_surface="wait_for_frames",
            source_authority="A",
            compatibility_status="compatible",
            status="active",
            unit_type="diagnostic",
            embedding=[1, 0, 0, 0],
        )
    )
    engine.put(
        NativeHybridDocument(
            record_id="obsolete",
            content="RealSense legacy timeout workaround",
            zh_content="旧相机方案",
            error_surface="timeout -12",
            symbol_surface="LegacyCamera retry",
            path_surface="legacy/camera.py",
            api_surface="retry",
            source_authority="B",
            compatibility_status="incompatible",
            status="superseded",
            unit_type="diagnostic",
            embedding=[0.9, 0.1, 0, 0],
        )
    )


def test_native_fulltext_vector_hybrid_and_filters(engine) -> None:
    _seed(engine)

    assert {
        item["record_id"]
        for item in engine.fulltext(field="content", query="RealSense timeout")
    } == {"current", "obsolete"}
    assert engine.fulltext(field="zh_content", query="相机")
    assert engine.fulltext(field="error_surface", query="-12")
    assert engine.fulltext(field="symbol_surface", query="wait_for_frames")[0][
        "record_id"
    ] == "current"
    assert engine.vector(embedding=[1, 0, 0, 0])[0]["record_id"] == "current"

    trace = engine.query(
        query="RealSense timeout -12 wait_for_frames",
        embedding=[1, 0, 0, 0],
        profile="PROFILE_ERROR",
        filters={"status": "active", "compatibility_status": "compatible"},
    )
    assert [item["record_id"] for item in trace.results] == ["current"]
    assert trace.results[0]["_semantic_score"] is not None
    assert "`status` = 'active'" in trace.generated_sql
    assert "`compatibility_status` = 'compatible'" in trace.generated_sql
    assert "_keyword_rank" in trace.generated_sql
    assert "_semantic_rank" in trace.generated_sql


def test_native_rerank_degrades_and_backup_restores(engine, tmp_path) -> None:
    _seed(engine)
    capability = engine.rerank_capability("model-that-does-not-exist")
    assert capability["available"] is False
    assert capability["fallback"] == "deterministic_rrf"

    backup = tmp_path / "native-hybrid.json"
    result = engine.logical_backup(backup)
    assert result["records"] == 2
    with engine.connection.cursor() as cursor:
        cursor.execute("DELETE FROM know_native_live_test")
    engine.connection.commit()

    assert engine.restore_logical_backup(backup)["records"] == 2
    assert engine.vector(embedding=[1, 0, 0, 0])[0]["record_id"] == "current"
