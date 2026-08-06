"""Pinned, read-only, bounded GitHub repository adapter."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import urllib.parse
from datetime import UTC, datetime
from typing import Any

from rosclaw_know.contracts import (
    IntegrityV2,
    ResearchRequestV2,
    SourceRecordV2,
    SourceSnapshotV2,
)
from rosclaw_know.store import DocumentRecord

from .base import SourceCandidate, SourceLimitError, SourceUnavailableError
from .http import HttpTransport, UrllibTransport
from .security import is_probably_binary, normalize_untrusted_text, safe_repo_path

_API = "https://api.github.com"
_INTERESTING_PREFIXES = (
    "docs/",
    "examples/",
    "configs/",
    "scripts/",
    "train/",
    "training/",
    "deploy/",
    "deployment/",
    "tests/",
    "src/",
    ".github/workflows/",
)
_INTERESTING_NAMES = {
    "readme",
    "readme.md",
    "license",
    "license.md",
    "dockerfile",
    "requirements.txt",
    "pyproject.toml",
    "package.xml",
    "setup.py",
    "setup.cfg",
    "cargo.toml",
    "cmakelists.txt",
}


def _id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:24]}"


class GitHubAdapter:
    name = "github"

    def __init__(
        self,
        *,
        token: str | None = None,
        transport: HttpTransport | None = None,
        timeout: float = 15.0,
        max_response_bytes: int = 5_000_000,
        max_documents: int = 200,
        max_document_bytes: int = 500_000,
        max_issue_documents: int = 20,
    ) -> None:
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.max_documents = max_documents
        self.max_document_bytes = max_document_bytes
        self.max_issue_documents = max_issue_documents
        self._snapshot_state: dict[str, dict[str, Any]] = {}

    @property
    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "rosclaw-know-v2/2",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get_json(self, path_or_url: str, *, max_bytes: int | None = None) -> Any:
        url = path_or_url if path_or_url.startswith("https://") else f"{_API}{path_or_url}"
        response = self.transport.get(
            url,
            headers=self._headers,
            timeout=self.timeout,
            max_bytes=max_bytes or self.max_response_bytes,
        )
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceUnavailableError(f"GitHub returned malformed JSON for {url}") from exc

    async def discover(self, request: ResearchRequestV2) -> list[SourceCandidate]:
        query = urllib.parse.urlencode(
            {
                "q": f"{request.topic} in:name,description,readme",
                "sort": "stars",
                "order": "desc",
                "per_page": min(request.max_sources, 50),
            }
        )
        payload = await asyncio.to_thread(self._get_json, f"/search/repositories?{query}")
        candidates = []
        for item in (payload.get("items") or [])[: request.max_sources]:
            full_name = str(item.get("full_name") or "")
            url = str(item.get("html_url") or "")
            if not full_name or not url:
                continue
            license_info = item.get("license") or {}
            candidates.append(
                SourceCandidate(
                    source=SourceRecordV2(
                        source_id=_id("source", url.casefold()),
                        canonical_url=url,
                        source_type="repository",
                        title=str(item.get("name") or full_name),
                        publisher=full_name.split("/", 1)[0],
                        repository=full_name,
                        license=license_info.get("spdx_id"),
                        trust_tier="primary",
                        discovered_at=datetime.now(UTC),
                        tags=list(item.get("topics") or []),
                    ),
                    adapter=self.name,
                    snapshot_ref=str(item.get("default_branch") or "HEAD"),
                    authority_score=min(
                        1.0, 0.55 + float(item.get("stargazers_count") or 0) / 100_000
                    ),
                    qualification_score=0.5,
                    metadata={
                        "full_name": full_name,
                        "default_branch": item.get("default_branch") or "HEAD",
                        "description": item.get("description"),
                        "stars": int(item.get("stargazers_count") or 0),
                    },
                )
            )
        return candidates

    async def snapshot(self, candidate: SourceCandidate) -> SourceSnapshotV2:
        full_name = str(candidate.metadata.get("full_name") or candidate.source.repository or "")
        if "/" not in full_name:
            raise ValueError("GitHub candidate requires owner/repository")
        ref = candidate.snapshot_ref or str(candidate.metadata.get("default_branch") or "HEAD")
        commit = await asyncio.to_thread(
            self._get_json, f"/repos/{full_name}/commits/{urllib.parse.quote(ref, safe='')}"
        )
        commit_sha = str(commit.get("sha") or "")
        tree_sha = str(((commit.get("commit") or {}).get("tree") or {}).get("sha") or "")
        if len(commit_sha) < 7 or not tree_sha:
            raise SourceUnavailableError(f"GitHub commit did not pin {full_name}@{ref}")
        tree = await asyncio.to_thread(
            self._get_json, f"/repos/{full_name}/git/trees/{tree_sha}?recursive=1"
        )
        if tree.get("truncated"):
            raise SourceLimitError(f"GitHub tree for {full_name}@{commit_sha} was truncated")
        descriptor = json.dumps(
            {"repository": full_name, "commit": commit_sha, "tree": tree_sha}, sort_keys=True
        )
        content_hash = hashlib.sha256(descriptor.encode()).hexdigest()
        snapshot_id = _id("snapshot", f"github:{full_name}:{commit_sha}")
        snapshot = SourceSnapshotV2(
            snapshot_id=snapshot_id,
            source_id=candidate.source.source_id,
            version_kind="git_commit",
            version_value=commit_sha,
            commit_sha=commit_sha,
            published_at=_parse_github_time(
                ((commit.get("commit") or {}).get("committer") or {}).get("date")
            ),
            fetched_at=datetime.now(UTC),
            content_hash=content_hash,
            integrity=IntegrityV2(sha256=content_hash),
        )
        self._snapshot_state[snapshot_id] = {
            "candidate": candidate,
            "commit": commit,
            "commit_sha": commit_sha,
            "tree": tree,
        }
        return snapshot

    def _selected_tree_paths(self, tree: list[dict[str, Any]]) -> list[str]:
        ranked = []
        for entry in tree:
            if entry.get("type") != "blob":
                continue
            try:
                path = safe_repo_path(str(entry.get("path") or ""))
            except ValueError:
                continue
            if is_probably_binary(path) or int(entry.get("size") or 0) > self.max_document_bytes:
                continue
            lower = path.casefold()
            name = lower.rsplit("/", 1)[-1]
            priority = (
                0
                if name in _INTERESTING_NAMES
                else 1
                if lower.startswith(_INTERESTING_PREFIXES)
                else 2
            )
            if priority < 2 or lower.endswith(
                (".py", ".cpp", ".hpp", ".h", ".yaml", ".yml", ".md")
            ):
                ranked.append((priority, len(path.split("/")), path))
        ranked.sort()
        return [path for _, _, path in ranked[: self.max_documents]]

    async def fetch_documents(self, snapshot: SourceSnapshotV2):
        state = self._snapshot_state.get(snapshot.snapshot_id)
        if state is None:
            raise SourceUnavailableError(
                "snapshot state unavailable; snapshot and fetch must use the same adapter instance"
            )
        candidate: SourceCandidate = state["candidate"]
        full_name = str(candidate.metadata["full_name"])
        commit_sha = str(state["commit_sha"])

        metadata_text = _json_pretty(
            {
                "repository": candidate.metadata,
                "commit": state["commit"],
                "tree_sha": (state["tree"] or {}).get("sha"),
            }
        )
        yield _document(
            snapshot,
            full_name,
            "repository_metadata.json",
            metadata_text,
            "repository_metadata",
            url=f"https://github.com/{full_name}/tree/{commit_sha}",
        )

        for path in self._selected_tree_paths(list((state["tree"] or {}).get("tree") or [])):
            encoded_path = urllib.parse.quote(path, safe="/")
            try:
                payload = await asyncio.to_thread(
                    self._get_json,
                    f"/repos/{full_name}/contents/{encoded_path}?ref={commit_sha}",
                    max_bytes=max(self.max_document_bytes * 2, 100_000),
                )
                if payload.get("encoding") != "base64":
                    continue
                raw = base64.b64decode(payload.get("content") or "", validate=False)
                if len(raw) > self.max_document_bytes or is_probably_binary(path, raw):
                    continue
                text, signals = normalize_untrusted_text(raw.decode("utf-8", errors="replace"))
            except (SourceUnavailableError, SourceLimitError, ValueError):
                continue
            yield _document(
                snapshot,
                full_name,
                path,
                text,
                "source_code"
                if path.rsplit(".", 1)[-1] in {"py", "cpp", "h", "hpp"}
                else "documentation",
                url=f"https://github.com/{full_name}/blob/{commit_sha}/{encoded_path}",
                prompt_injection_signals=signals,
            )

        endpoint_specs = (
            ("releases", "release", "releases?per_page=10"),
            (
                "issues",
                "issue",
                f"issues?state=all&sort=comments&direction=desc&per_page={self.max_issue_documents}",
            ),
            (
                "pull_requests",
                "pull_request",
                f"pulls?state=all&sort=updated&direction=desc&per_page={self.max_issue_documents}",
            ),
            ("languages", "repository_metadata", "languages"),
            ("tags", "release", "tags?per_page=20"),
        )
        for name, document_type, endpoint in endpoint_specs:
            try:
                payload = await asyncio.to_thread(self._get_json, f"/repos/{full_name}/{endpoint}")
            except SourceUnavailableError:
                continue
            text, signals = normalize_untrusted_text(_json_pretty(payload))
            yield _document(
                snapshot,
                full_name,
                f".rosclaw/github/{name}.json",
                text,
                document_type,
                url=f"https://github.com/{full_name}/{name}",
                prompt_injection_signals=signals,
            )


def _parse_github_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _document(
    snapshot: SourceSnapshotV2,
    repository: str,
    path: str,
    content: str,
    document_type: str,
    *,
    url: str,
    prompt_injection_signals: list[str] | None = None,
) -> DocumentRecord:
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    return DocumentRecord(
        document_id=_id("document", f"{snapshot.snapshot_id}:{path}:{content_hash}"),
        snapshot_id=snapshot.snapshot_id,
        document_type=document_type,
        path=path,
        title=path.rsplit("/", 1)[-1],
        language=_language(path),
        content=content,
        content_hash=content_hash,
        size_bytes=len(content.encode()),
        metadata={
            "repository": repository,
            "url": url,
            "untrusted_source": True,
            "prompt_injection_signals": prompt_injection_signals or [],
            "code_executed": False,
        },
        created_at=datetime.now(UTC),
    )


def _language(path: str) -> str | None:
    suffix = path.casefold().rsplit(".", 1)[-1] if "." in path else ""
    return {
        "py": "python",
        "cpp": "cpp",
        "hpp": "cpp",
        "h": "c",
        "rs": "rust",
        "md": "markdown",
        "yaml": "yaml",
        "yml": "yaml",
        "json": "json",
        "toml": "toml",
    }.get(suffix)
