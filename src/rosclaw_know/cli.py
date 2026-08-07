"""Operational CLI for the final Know acceptance surface."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rosclaw_know.contracts import ReferenceContextV2
from rosclaw_know.operations import (
    audit_project,
    doctor,
    freeze,
    project_diff,
    refresh_source,
)
from rosclaw_know.retrieval import ReferencePackBuilder
from rosclaw_know.store import KnowStore, create_know_store


def _print(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _store(args: argparse.Namespace) -> KnowStore:
    mode = args.store_mode
    if mode == "memory":
        return create_know_store(mode="memory", allow_test_memory=True)
    common: dict[str, Any] = {
        "database": os.environ.get("ROSCLAW_KNOW_DATABASE", "rosclaw_know"),
        "memory_database": os.environ.get("ROSCLAW_MEMORY_DATABASE"),
        "practice_database": os.environ.get("ROSCLAW_PRACTICE_DATABASE"),
    }
    if mode == "embedded":
        return create_know_store(
            mode=mode,
            path=args.store_path,
            memory_path=os.environ.get("ROSCLAW_MEMORY_SEEKDB_PATH"),
            practice_path=os.environ.get("ROSCLAW_PRACTICE_SEEKDB_PATH"),
            **common,
        )
    return create_know_store(
        mode=mode,
        host=args.host,
        port=args.port,
        tenant=args.tenant,
        user=args.user,
        password=os.environ.get("SEEKDB_PASSWORD", ""),
        **common,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rosclaw-know")
    parser.add_argument(
        "--store-mode",
        choices=["embedded", "server", "memory"],
        default=os.environ.get("ROSCLAW_KNOW_STORE_MODE", "embedded"),
    )
    parser.add_argument(
        "--store-path",
        default=os.environ.get(
            "ROSCLAW_KNOW_SEEKDB_PATH", str(Path.cwd() / "data" / "know" / "seekdb")
        ),
    )
    parser.add_argument("--host", default=os.environ.get("SEEKDB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SEEKDB_PORT", "2881")))
    parser.add_argument("--tenant", default=os.environ.get("SEEKDB_TENANT", "sys"))
    parser.add_argument("--user", default=os.environ.get("SEEKDB_USER", "root"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    explain = commands.add_parser("explain")
    explain.add_argument("query")
    explain.add_argument("--robot")
    explain.add_argument("--simulator")
    explain.add_argument("--ros-distro")
    explain.add_argument("--failure")
    explain.add_argument("--top-k", type=int, default=10)
    diff = commands.add_parser("diff")
    diff.add_argument("project_id")
    diff.add_argument("--from", dest="from_snapshot", required=True)
    diff.add_argument("--to", dest="to_snapshot", required=True)
    refresh = commands.add_parser("refresh")
    refresh.add_argument("source_id")
    refresh.add_argument("--apply", action="store_true")
    audit = commands.add_parser("audit")
    audit.add_argument("project_id")
    review = commands.add_parser("review")
    review_subcommands = review.add_subparsers(dest="review_command", required=True)
    review_subcommands.add_parser("queue")
    show = review_subcommands.add_parser("show")
    show.add_argument("review_id")
    for action in ("apply", "reject"):
        command = review_subcommands.add_parser(action)
        command.add_argument("review_id")
        command.add_argument("--resolution")
    freeze_parser = commands.add_parser("freeze")
    freeze_parser.add_argument("--label", required=True)
    freeze_parser.add_argument("--output")
    return parser


def _review(store: KnowStore, args: argparse.Namespace) -> dict[str, Any]:
    if args.review_command == "queue":
        feedback = store.list_feedback_governance(status="pending_review", limit=1000)
        disagreements = store.list_source_disagreements(status="pending_review", limit=1000)
        return {
            "automatic_mutation_allowed": False,
            "feedback": [item.model_dump(mode="json") for item in feedback],
            "source_disagreements": [item.model_dump(mode="json") for item in disagreements],
        }
    feedback = store.get_feedback_governance(args.review_id)
    disagreement = store.get_source_disagreement(args.review_id)
    if args.review_command == "show":
        if feedback:
            return {"kind": "feedback", "record": feedback.model_dump(mode="json")}
        if disagreement:
            return {
                "kind": "source_disagreement",
                "record": disagreement.model_dump(mode="json"),
            }
        raise ValueError(f"unknown review id: {args.review_id}")
    decision = "apply" if args.review_command == "apply" else "reject"
    if feedback:
        updated = store.review_feedback_governance(args.review_id, decision=decision)
        return {
            "kind": "feedback",
            "decision": decision,
            "knowledge_mutated": False,
            "record": updated.model_dump(mode="json") if updated else None,
        }
    if disagreement:
        status = "reviewed" if decision == "apply" else "dismissed"
        if status == "reviewed" and not args.resolution:
            raise ValueError("applying a source-disagreement review requires --resolution")
        updated = disagreement.model_copy(
            update={
                "status": status,
                "resolution": args.resolution,
                "updated_at": datetime.now(UTC),
            }
        )
        store.put_source_disagreement(updated)
        return {
            "kind": "source_disagreement",
            "decision": decision,
            "knowledge_mutated": False,
            "record": updated.model_dump(mode="json"),
        }
    raise ValueError(f"unknown review id: {args.review_id}")


def main() -> None:
    args = _parser().parse_args()
    store = _store(args)
    try:
        if args.command == "doctor":
            _print(doctor(store))
        elif args.command == "explain":
            context = ReferenceContextV2(
                robot=args.robot,
                simulator=args.simulator,
                ros_distro=args.ros_distro,
                current_failure=args.failure,
            )
            _print(
                ReferencePackBuilder(store).explain(
                    query=args.query, context=context, top_k=args.top_k
                )
            )
        elif args.command == "diff":
            _print(
                project_diff(
                    store,
                    project_id=args.project_id,
                    from_snapshot=args.from_snapshot,
                    to_snapshot=args.to_snapshot,
                )
            )
        elif args.command == "refresh":
            _print(asyncio.run(refresh_source(store, source_id=args.source_id, apply=args.apply)))
        elif args.command == "audit":
            _print(audit_project(store, args.project_id))
        elif args.command == "review":
            _print(_review(store, args))
        elif args.command == "freeze":
            manifest = freeze(store, label=args.label)
            if args.output:
                output = Path(args.output).expanduser().resolve(strict=False)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            _print(manifest)
    finally:
        store.close()


__all__ = ["main"]
