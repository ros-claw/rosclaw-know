"""Paired vLLM check: unsupported guessing versus pinned Reference Pack evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from rosclaw_know.contracts import (
    EvidenceRefV2,
    ReferenceContextV2,
    ReferencePackItemV2,
    ReferencePackV2,
)

CASES = (
    {
        "id": "deepwiki_adapter",
        "repository": "rosclaw-know",
        "file": "src/rosclaw_know/sources/external_mcp.py",
        "symbol": "DeepWikiPublicAdapter",
        "question": "负责接入 DeepWiki Public MCP 的生产 adapter 的准确文件和类名是什么？",
    },
    {
        "id": "seekdb_native_hybrid",
        "repository": "rosclaw-know",
        "file": "src/rosclaw_know/store/server_native.py",
        "symbol": "NativeHybridQueryEngine",
        "question": "负责 SeekDB server 原生全文、向量和 RRF 混合检索的准确文件和类名是什么？",
    },
    {
        "id": "how_explain_handler",
        "repository": "rosclaw-how",
        "file": "src/rosclaw_how/api.py",
        "symbol": "how_v2_explain_advice",
        "question": "How v2 提供 advice 结构化解释的 handler 在哪个准确文件，函数名是什么？",
    },
)


def _evidence(case: dict[str, str], root: Path) -> tuple[EvidenceRefV2, str]:
    path = root / case["repository"] / case["file"]
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    number = next(index for index, line in enumerate(lines, start=1) if case["symbol"] in line)
    start = max(1, number - 5)
    end = min(len(lines), number + 5)
    excerpt = "\n".join(lines[start - 1 : end])
    digest = hashlib.sha256(content.encode()).hexdigest()
    snapshot_id = f"snapshot_content_{digest[:24]}"
    evidence = EvidenceRefV2(
        evidence_id=f"evidence_{hashlib.sha256(f'{path}:{start}:{end}'.encode()).hexdigest()[:24]}",
        source_id=f"source_{case['repository']}",
        snapshot_id=snapshot_id,
        document_id=f"document_{digest[:24]}",
        path=case["file"],
        start_line=start,
        end_line=end,
        url=f"local-audit://{case['repository']}/{case['file']}#L{start}-L{end}",
        content_hash=digest,
        excerpt=excerpt,
    )
    return evidence, snapshot_id


def _pack(case: dict[str, str], root: Path) -> ReferencePackV2:
    evidence, snapshot_id = _evidence(case, root)
    return ReferencePackV2(
        reference_pack_id=f"reference_pack_{case['id']}",
        query=case["question"],
        context=ReferenceContextV2(task="locate exact implementation"),
        generated_at=datetime.now(UTC),
        index_version="working-tree-content-hash",
        items=[
            ReferencePackItemV2(
                rank=1,
                knowledge_unit_ids=[f"unit_{case['id']}"],
                title=case["id"],
                why_relevant="Exact implementation symbol matched pinned source text.",
                relevance_dimensions=["exact_symbol", "path"],
                mechanism="The cited definition is the implementation boundary.",
                what_to_borrow=["Open the cited source before editing."],
                exact_files=[case["file"]],
                source_version=f"content_hash:{snapshot_id}",
                evidence_refs=[evidence],
                score=1.0,
                score_breakdown={"exact": 1.0, "truth_quality": 1.0},
            )
        ],
        recommended_reading_order=[f"unit_{case['id']}"],
        token_budget=4_000,
    )


def _call(endpoint: str, model: str, prompt: str) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 128,
        },
        ensure_ascii=False,
    ).encode()
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with opener.open(request, timeout=45.0) as response:
                payload = json.loads(response.read(5_000_000))
            return str(payload["choices"][0]["message"]["content"])
        except (TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
    raise RuntimeError("vLLM request failed after three bounded attempts") from last_error


def _answer_json(text: str) -> dict[str, str]:
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        return {"file": "", "symbol": ""}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"file": "", "symbol": ""}
    return {"file": str(value.get("file") or ""), "symbol": str(value.get("symbol") or "")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", default="deepseekv4")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    instructions = (
        "只返回严格 JSON：{\"file\":\"...\",\"symbol\":\"...\"}。"
        "只能使用提供的证据；证据不足时两项都回答 unknown，禁止猜测。\n问题："
    )
    results = []
    for case in CASES:
        pack = _pack(case, args.workspace_root)
        baseline_text = _call(args.endpoint, args.model, instructions + case["question"])
        grounded_text = _call(
            args.endpoint,
            args.model,
            instructions
            + case["question"]
            + "\nROSClaw Reference Pack：\n"
            + pack.model_dump_json(indent=2),
        )
        baseline = _answer_json(baseline_text)
        grounded = _answer_json(grounded_text)
        expected = {"file": case["file"], "symbol": case["symbol"]}
        results.append(
            {
                "case_id": case["id"],
                "expected": expected,
                "baseline": baseline,
                "grounded": grounded,
                "baseline_passed": baseline == expected,
                "grounded_passed": grounded == expected,
                "reference_pack_id": pack.reference_pack_id,
                "evidence_id": pack.items[0].evidence_refs[0].evidence_id,
            }
        )
    report = {
        "schema_version": "rosclaw.know.vllm_paired_acceptance.v1",
        "model": args.model,
        "temperature": 0.0,
        "baseline_passed": sum(item["baseline_passed"] for item in results),
        "grounded_passed": sum(item["grounded_passed"] for item in results),
        "total": len(results),
        "results": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["grounded_passed"] == report["total"] else 1)


if __name__ == "__main__":
    main()
