"""Sprint 6: tests for the EvidenceTrace JSONL helpers."""
from __future__ import annotations

import json
from pathlib import Path

from rosclaw_know.evidence_writer import (
    EvidenceTraceWriter,
    compute_code_diff_hash,
    detect_hint_use,
    stream_traces,
    temp_writer,
)
from rosclaw_know.schemas import EvidenceTrace

# ── factory ──────────────────────────────────────────────────────────────


def _make_trace(
    *,
    trace_id: str = "trace_001",
    pattern_id: str = "compiled_anti_windup",
    arm: str = "true",
    strategy: str = "CATALYST",
    pre_score: float = 0.5,
    post_score_5: float | None = 0.65,
    used_hint: bool = True,
    code_diff_summary: list[str] | None = None,
) -> EvidenceTrace:
    delta = (post_score_5 - pre_score) if post_score_5 is not None else None
    return EvidenceTrace(
        trace_id=trace_id,
        run_id="run_a",
        task_name="PIDTuning",
        iteration=4,
        injection_id="inj_001",
        pattern_id=pattern_id,
        strategy=strategy,
        pre_score=pre_score,
        post_score_5=post_score_5,
        best_delta_5=delta,
        code_diff_summary=code_diff_summary or [],
        used_hint=used_hint,
        verifier_status="valid",
        objective_direction="maximize",
        arm=arm,
    )


# ── code-diff hash ───────────────────────────────────────────────────────


def test_code_diff_hash_is_deterministic() -> None:
    h1 = compute_code_diff_hash("x = 1", "x = 2")
    h2 = compute_code_diff_hash("x = 1", "x = 2")
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_code_diff_hash_changes_with_content() -> None:
    h1 = compute_code_diff_hash("x = 1", "x = 2")
    h2 = compute_code_diff_hash("x = 1", "x = 3")
    assert h1 != h2


def test_code_diff_hash_ignores_comments() -> None:
    """Adding a comment to either side should not change the hash."""
    h1 = compute_code_diff_hash("x = 1", "x = 2")
    h2 = compute_code_diff_hash("# this is x\nx = 1", "# this is x\nx = 2")
    assert h1 == h2


def test_code_diff_hash_ignores_trailing_whitespace() -> None:
    h1 = compute_code_diff_hash("x = 1", "x = 2")
    h2 = compute_code_diff_hash("x = 1   \n", "x = 2  \n")
    assert h1 == h2


def test_code_diff_hash_ignores_blank_lines() -> None:
    h1 = compute_code_diff_hash("x = 1", "x = 2")
    h2 = compute_code_diff_hash("x = 1\n\n\n", "\nx = 2\n\n")
    assert h1 == h2


def test_code_diff_hash_distinguishes_before_after() -> None:
    """Swapping before+after must change the hash."""
    h1 = compute_code_diff_hash("x = 1", "x = 2")
    h2 = compute_code_diff_hash("x = 2", "x = 1")
    assert h1 != h2


# ── detect_hint_use ─────────────────────────────────────────────────────


def test_detect_hint_use_matches_keyword() -> None:
    used, matched = detect_hint_use(
        ["set Ki_z to zero on saturation"],
        [r"zero[\s_-]+integral", r"clamp"],
    )
    assert used is False  # "set Ki_z to zero" doesn't contain "zero_integral"
    assert matched == []


def test_detect_hint_use_finds_pattern() -> None:
    used, matched = detect_hint_use(
        ["added output clamp before actuator"],
        [r"clamp", r"saturat"],
    )
    assert used is True
    assert "clamp" in matched


def test_detect_hint_use_case_insensitive() -> None:
    used, matched = detect_hint_use(
        ["Added Output CLAMP"],
        [r"clamp"],
    )
    assert used is True


def test_detect_hint_use_empty_inputs_safe() -> None:
    assert detect_hint_use([], [r"foo"]) == (False, [])
    assert detect_hint_use(["bar"], []) == (False, [])


def test_detect_hint_use_skips_bad_regex(caplog) -> None:
    used, matched = detect_hint_use(
        ["test"],
        [r"(unclosed", r"test"],
    )
    assert used is True
    assert matched == ["test"]


# ── EvidenceTraceWriter ─────────────────────────────────────────────────


def test_writer_round_trip_via_streamer(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    t1 = _make_trace(trace_id="t1")
    t2 = _make_trace(trace_id="t2", arm="placebo", post_score_5=0.52, used_hint=False)
    with EvidenceTraceWriter(path) as w:
        w.append(t1)
        w.append(t2)
    assert path.is_file()
    out = list(stream_traces(path))
    assert len(out) == 2
    assert {t.trace_id for t in out} == {"t1", "t2"}


def test_writer_append_many(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    with EvidenceTraceWriter(path) as w:
        n = w.append_many([_make_trace(trace_id=f"t{i}") for i in range(5)])
    assert n == 5
    out = list(stream_traces(path))
    assert len(out) == 5


def test_writer_records_count_tracked(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    w = EvidenceTraceWriter(path)
    assert w.record_count == 0
    w.append(_make_trace())
    assert w.record_count == 1
    w.close()


def test_writer_context_manager_closes(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    with temp_writer(path) as w:
        w.append(_make_trace())
    # After close, reopening for read should work
    assert list(stream_traces(path))


def test_writer_jsonl_is_valid(tmp_path: Path) -> None:
    """Each line of the output must round-trip through json.loads."""
    path = tmp_path / "traces.jsonl"
    traces = [_make_trace(trace_id=f"t{i}") for i in range(3)]
    with EvidenceTraceWriter(path) as w:
        for t in traces:
            w.append(t)
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.split("\n") if ln]
    assert len(lines) == 3
    for ln in lines:
        obj = json.loads(ln)
        assert obj["schema_version"] == "2.0"


# ── stream_traces ───────────────────────────────────────────────────────


def test_stream_traces_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text(
        '{"trace_id": "ok", "run_id": "r", "task_name": "t", "iteration": 1, '
        '"strategy": "NONE", "pre_score": 0.5, "objective_direction": "maximize", '
        '"arm": "baseline"}\n'
        'this is not json\n'
        '{"missing": "required-fields"}\n',
        encoding="utf-8",
    )
    out = list(stream_traces(path))
    # The first line is a valid EvidenceTrace; the other two are skipped.
    assert len(out) == 1
    assert out[0].trace_id == "ok"


def test_stream_traces_missing_file_is_empty() -> None:
    out = list(stream_traces(Path("/tmp/does_not_exist_xyz.jsonl")))
    assert out == []
