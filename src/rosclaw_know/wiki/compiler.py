"""Evidence-linked, bounded Project Wiki compiler.

The compiler performs static parsing only. It never imports repository code,
runs build tools, installs dependencies, or trusts source instructions.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
import tomllib
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

import yaml

from rosclaw_know.contracts import EvidenceRefV2, ProjectCardV2, SourceRecordV2, SourceSnapshotV2
from rosclaw_know.store import DocumentRecord, KnowStore, ProjectComponentRecord, WikiPageRecord

from .models import RepositoryInventory, WikiCompilationResult

_BUILD_FILES = {
    "pyproject.toml": "pyproject",
    "setup.py": "setuptools",
    "setup.cfg": "setuptools",
    "cmakelists.txt": "cmake",
    "package.xml": "ros_package",
    "cargo.toml": "cargo",
    "package.json": "npm",
}
_FRAMEWORK_MARKERS = {
    "isaac lab": "isaac_lab",
    "isaac sim": "isaac_sim",
    "mujoco": "mujoco",
    "gazebo": "gazebo",
    "pytorch": "pytorch",
    "torch": "pytorch",
    "tensorflow": "tensorflow",
    "stable_baselines": "stable_baselines",
    "rsl_rl": "rsl_rl",
}
_ROBOT_MARKERS = {
    "unitree g1": "unitree_g1",
    "unitree_g1": "unitree_g1",
    "unitree h1": "unitree_h1",
    "humanoid": "humanoid",
    "quadruped": "quadruped",
}
_PAGE_RULES = {
    "overview": lambda path, text: path.casefold().startswith("readme") or "repository_metadata" in path,
    "architecture": lambda path, text: path.startswith(("src/", "source/")),
    "training": lambda path, text: any(part in path.casefold() for part in ("train", "policy", "reward", "env")),
    "deployment": lambda path, text: any(part in path.casefold() for part in ("deploy", "hardware", "docker")),
    "robot_interface": lambda path, text: any(part in text.casefold() for part in ("topic", "service", "action space", "urdf", "mjcf", "usd")),
    "issues_and_releases": lambda path, text: path.startswith(".rosclaw/github/"),
    "configuration": lambda path, text: path.casefold().endswith((".yaml", ".yml", ".toml", ".json")),
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _identifier(prefix: str, value: str) -> str:
    return f"{prefix}_{_hash(value)[:24]}"


def _steering(documents: list[DocumentRecord]) -> dict[str, Any]:
    doc = next((item for item in documents if item.path == ".rosclaw/know.yaml"), None)
    if doc is None:
        return {}
    try:
        payload = yaml.safe_load(doc.content) or {}
    except yaml.YAMLError:
        return {"warnings": ["invalid .rosclaw/know.yaml ignored"]}
    if not isinstance(payload, dict) or payload.get("schema_version") not in {
        None,
        "rosclaw.know.steer.v1",
    }:
        return {"warnings": ["unsupported .rosclaw/know.yaml ignored"]}
    return payload


def _apply_steering(documents: list[DocumentRecord], steering: dict[str, Any]) -> list[DocumentRecord]:
    excludes = [str(item) for item in steering.get("exclude", []) if isinstance(item, str)]
    priority = [str(item) for item in steering.get("priority_paths", []) if isinstance(item, str)]
    visible = [
        document
        for document in documents
        if not any(fnmatch.fnmatch(document.path, pattern) for pattern in excludes)
    ]
    if priority:
        visible.sort(
            key=lambda item: (
                not any(fnmatch.fnmatch(item.path, pattern) for pattern in priority),
                item.path,
            )
        )
    else:
        visible.sort(key=lambda item: item.path)
    return visible


def build_inventory(documents: list[DocumentRecord]) -> RepositoryInventory:
    paths = sorted({document.path for document in documents})
    lower_paths = {path.casefold() for path in paths}
    corpus = "\n".join(document.content[:50_000] for document in documents).casefold()
    build_systems = sorted(
        {system for path, system in _BUILD_FILES.items() if path in lower_paths}
    )
    package_managers = sorted(
        {
            manager
            for marker, manager in (
                ("pyproject.toml", "python"),
                ("requirements.txt", "pip"),
                ("package.xml", "rosdep"),
                ("package.json", "npm"),
                ("cargo.toml", "cargo"),
            )
            if marker in lower_paths
        }
    )
    frameworks = sorted({value for marker, value in _FRAMEWORK_MARKERS.items() if marker in corpus})
    robots = sorted({value for marker, value in _ROBOT_MARKERS.items() if marker in corpus})
    simulators = sorted(
        set(frameworks) & {"isaac_lab", "isaac_sim", "mujoco", "gazebo"}
    )
    ros_distros = sorted(
        distro for distro in ("noetic", "foxy", "galactic", "humble", "iron", "jazzy") if distro in corpus
    )
    unknowns = []
    if not robots:
        unknowns.append("supported robot model is not stated in indexed documents")
    if not simulators:
        unknowns.append("simulator is not stated in indexed documents")
    file_symbols: dict[str, list[str]] = {}
    file_imports: dict[str, list[str]] = {}
    entrypoints: list[str] = []
    versions: dict[str, str] = {}
    config_keys: dict[str, list[str]] = {}
    releases: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []
    pull_requests: list[dict[str, str]] = []
    for document in documents:
        if document.language == "python":
            symbols, imports, detected_entrypoints = _python_structure(document.content)
            if symbols:
                file_symbols[document.path] = symbols
            if imports:
                file_imports[document.path] = imports
            entrypoints.extend(
                f"{document.path}:{entrypoint}" for entrypoint in detected_entrypoints
            )
        if document.path.casefold() == "pyproject.toml":
            try:
                project = tomllib.loads(document.content).get("project") or {}
                if project.get("requires-python"):
                    versions["python"] = str(project["requires-python"])
                for dependency in project.get("dependencies") or []:
                    match = re.match(r"([A-Za-z0-9_.-]+)\s*([^;]*)", str(dependency))
                    if match:
                        versions[match.group(1).casefold()] = match.group(2).strip() or "unspecified"
            except (tomllib.TOMLDecodeError, AttributeError):
                pass
        if document.path.casefold().endswith((".yaml", ".yml")):
            try:
                config = yaml.safe_load(document.content)
                if isinstance(config, dict):
                    config_keys[document.path] = sorted(str(key) for key in config)[:200]
            except yaml.YAMLError:
                pass
        if document.path.startswith(".rosclaw/github/"):
            try:
                payload = json.loads(document.content)
            except json.JSONDecodeError:
                continue
            rows = payload if isinstance(payload, list) else []
            target = (
                releases
                if "releases.json" in document.path
                else pull_requests
                if "pull_requests.json" in document.path
                else issues
                if "issues.json" in document.path
                else None
            )
            if target is not None:
                for row in rows[:100]:
                    if not isinstance(row, dict):
                        continue
                    target.append(
                        {
                            "id": str(row.get("number") or row.get("id") or row.get("tag_name") or ""),
                            "title": str(row.get("title") or row.get("name") or ""),
                            "state": str(row.get("state") or ("published" if row.get("published_at") else "")),
                            "updated_at": str(row.get("updated_at") or row.get("published_at") or ""),
                            "tag": str(row.get("tag_name") or ""),
                            "body": str(row.get("body") or "")[:4000],
                        }
                    )
    return RepositoryInventory(
        paths=paths,
        languages=sorted({document.language for document in documents if document.language}),
        build_systems=build_systems,
        package_managers=package_managers,
        frameworks=frameworks,
        ros_distros=ros_distros,
        simulators=simulators,
        robots=robots,
        has_ci=any(path.startswith(".github/workflows/") for path in paths),
        has_container=any(PurePosixPath(path).name.casefold() == "dockerfile" for path in paths),
        has_docs=any(path.startswith("docs/") for path in paths),
        has_examples=any(path.startswith("examples/") for path in paths),
        has_tests=any(path.startswith(("test/", "tests/")) for path in paths),
        unknowns=unknowns,
        file_symbols=file_symbols,
        file_imports=file_imports,
        entrypoints=sorted(set(entrypoints)),
        versions=dict(sorted(versions.items())),
        config_keys=dict(sorted(config_keys.items())),
        releases=releases,
        issues=issues,
        pull_requests=pull_requests,
    )


def _python_structure(content: str) -> tuple[list[str], list[str], list[str]]:
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return [], [], []
    symbols = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ][:100]
    dependencies = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            dependencies.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            dependencies.append(node.module)
    entrypoints = ["__main__"] if 'if __name__ == "__main__"' in content else []
    return symbols, sorted(set(dependencies))[:100], entrypoints


def _generic_structure(content: str) -> tuple[list[str], list[str], list[str]]:
    symbols = re.findall(
        r"(?:class|struct|def|function)\s+([A-Za-z_][A-Za-z0-9_]*)", content
    )[:100]
    return list(dict.fromkeys(symbols)), [], []


def build_components(
    project_id: str, snapshot_id: str, documents: list[DocumentRecord]
) -> list[ProjectComponentRecord]:
    grouped: dict[str, list[DocumentRecord]] = defaultdict(list)
    for document in documents:
        # Acquisition metadata is evidence about the snapshot, not a project
        # architecture component.
        if document.path.startswith(".rosclaw/") or document.path == "repository_metadata.json":
            continue
        root = document.path.split("/", 1)[0]
        grouped[root].append(document)
    components = []
    for root, items in sorted(grouped.items()):
        symbols: list[str] = []
        dependencies: list[str] = []
        entrypoints: list[str] = []
        for item in items[:100]:
            structure = (
                _python_structure(item.content)
                if item.language == "python"
                else _generic_structure(item.content)
            )
            symbols.extend(structure[0])
            dependencies.extend(structure[1])
            entrypoints.extend(structure[2])
        digest = _hash("".join(sorted(item.content_hash for item in items)))
        components.append(
            ProjectComponentRecord(
                component_id=_identifier("component", f"{snapshot_id}:{root}"),
                project_id=project_id,
                snapshot_id=snapshot_id,
                component_type="module" if len(items) > 1 else "file",
                path=root,
                language=next((item.language for item in items if item.language), None),
                responsibility=f"Contains {len(items)} indexed file(s); responsibility beyond indexed evidence is unknown.",
                public_symbols=list(dict.fromkeys(symbols))[:100],
                dependencies=sorted(set(dependencies))[:100],
                entrypoints=sorted(set(entrypoints))[:50],
                content_hash=digest,
            )
        )
    return components


def _evidence_for(source: SourceRecordV2, snapshot: SourceSnapshotV2, document: DocumentRecord):
    lines = document.content.splitlines()
    excerpt_lines = lines[: min(12, len(lines))]
    excerpt = "\n".join(excerpt_lines).strip()[:2000] or "Indexed empty document metadata."
    return EvidenceRefV2(
        evidence_id=_identifier("evidence", f"{snapshot.snapshot_id}:{document.document_id}:1:{len(excerpt_lines)}"),
        source_id=source.source_id,
        snapshot_id=snapshot.snapshot_id,
        document_id=document.document_id,
        path=document.path,
        start_line=1,
        end_line=max(1, len(excerpt_lines)),
        section=None,
        url=str(document.metadata.get("url") or source.canonical_url),
        content_hash=document.content_hash,
        excerpt=excerpt,
    )


def _page_content(
    title: str,
    page_type: str,
    documents: list[DocumentRecord],
    inventory: RepositoryInventory,
) -> str:
    paths = [document.path for document in documents]
    facts = [f"# {title}", "", "## Indexed evidence", ""]
    facts.extend(f"- `{path}`" for path in paths)
    facts.extend(["", "## Verified inventory", ""])
    if page_type == "overview":
        facts.extend(
            (
                f"- Languages: {', '.join(inventory.languages) or 'unknown'}",
                f"- Frameworks: {', '.join(inventory.frameworks) or 'unknown'}",
                f"- Robots: {', '.join(inventory.robots) or 'unknown'}",
                f"- Simulators: {', '.join(inventory.simulators) or 'unknown'}",
                f"- Indexed files: {len(inventory.paths)}",
                f"- Deterministic symbols: {sum(len(items) for items in inventory.file_symbols.values())}",
            )
        )
    else:
        facts.append(
            "- Claims beyond the indexed paths and bounded excerpts remain unknown until additional evidence is ingested."
        )
    return "\n".join(facts) + "\n"


def _changed_paths(
    documents: list[DocumentRecord], previous_documents: list[DocumentRecord] | None
) -> list[str]:
    if previous_documents is None:
        return sorted(document.path for document in documents)
    current = {document.path: document.content_hash for document in documents}
    previous = {document.path: document.content_hash for document in previous_documents}
    return sorted(path for path in current.keys() | previous.keys() if current.get(path) != previous.get(path))


def _repo_facts_document(
    *,
    source: SourceRecordV2,
    snapshot: SourceSnapshotV2,
    inventory: RepositoryInventory,
    components: list[ProjectComponentRecord],
    documents: list[DocumentRecord],
) -> DocumentRecord:
    """Materialize deterministic Phase-A facts without executing source code."""

    payload = {
        "schema_version": "rosclaw.know.repo_facts.v1",
        "source_id": source.source_id,
        "snapshot_id": snapshot.snapshot_id,
        "version": snapshot.version_value,
        "component_count": len(components),
        "component_paths": [component.path for component in components],
        "inventory": inventory.model_dump(mode="json"),
        "source_documents": [
            {"path": document.path, "content_hash": document.content_hash}
            for document in documents
        ],
        "code_executed": False,
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    content_hash = _hash(content)
    return DocumentRecord(
        document_id=_identifier(
            "document", f"{snapshot.snapshot_id}:.rosclaw/repo_facts.json:{content_hash}"
        ),
        snapshot_id=snapshot.snapshot_id,
        document_type="deterministic_fact_inventory",
        path=".rosclaw/repo_facts.json",
        title="repo_facts.json",
        language="json",
        content=content,
        content_hash=content_hash,
        size_bytes=len(content.encode()),
        metadata={
            "url": (
                f"rosclaw-know://snapshot/{snapshot.snapshot_id}/"
                ".rosclaw/repo_facts.json"
            ),
            "generated_by": "rosclaw_know.wiki.compiler:deterministic_facts",
            "code_executed": False,
            "source_document_hashes": [document.content_hash for document in documents],
        },
        created_at=datetime.now(UTC),
    )


def compile_project_wiki(
    *,
    source: SourceRecordV2,
    snapshot: SourceSnapshotV2,
    documents: list[DocumentRecord],
    previous_documents: list[DocumentRecord] | None = None,
    store: KnowStore | None = None,
) -> WikiCompilationResult:
    if not documents:
        raise ValueError("Project Wiki compilation requires indexed documents")
    if any(document.snapshot_id != snapshot.snapshot_id for document in documents):
        raise ValueError("all documents must belong to the compiled snapshot")
    steer = _steering(documents)
    selected = _apply_steering(documents, steer)
    if not selected:
        raise ValueError("steering configuration excluded every indexed document")
    inventory = build_inventory(selected)
    project_id = _identifier("project", source.canonical_url.casefold())
    evidence_by_document = {
        document.document_id: _evidence_for(source, snapshot, document) for document in selected
    }
    components = build_components(project_id, snapshot.snapshot_id, selected)
    facts_document = _repo_facts_document(
        source=source,
        snapshot=snapshot,
        inventory=inventory,
        components=components,
        documents=selected,
    )
    facts_evidence = _evidence_for(source, snapshot, facts_document)
    changed = _changed_paths(selected, previous_documents)

    pages = []
    rebuilt = []
    for order, (page_type, rule) in enumerate(_PAGE_RULES.items()):
        matched = [document for document in selected if rule(document.path, document.content)]
        if not matched and page_type != "overview":
            continue
        matched = matched or selected[:1]
        title = page_type.replace("_", " ").title()
        content = _page_content(title, page_type, matched, inventory)
        evidence = [evidence_by_document[item.document_id] for item in matched[:20]]
        pages.append(
            WikiPageRecord(
                page_id=_identifier("page", f"{snapshot.snapshot_id}:{page_type}"),
                snapshot_id=snapshot.snapshot_id,
                project_id=project_id,
                page_type=page_type,
                title=title,
                slug=page_type.replace("_", "-"),
                summary=f"Evidence-linked {title.lower()} for {source.title}.",
                content=content,
                outline_order=order,
                content_hash=_hash(content),
                evidence_refs=evidence,
                created_at=datetime.now(UTC),
            )
        )
        if previous_documents is None or any(item.path in changed for item in matched):
            rebuilt.append(page_type)

    root_page = next(page.page_id for page in pages if page.page_type == "overview")
    all_evidence = [facts_evidence, *evidence_by_document.values()]
    issue_paths = [document.path for document in selected if "issues.json" in document.path]
    pull_paths = [document.path for document in selected if "pull_requests.json" in document.path]
    card = ProjectCardV2(
        project_id=project_id,
        source_snapshot_id=snapshot.snapshot_id,
        name=source.title,
        summary=f"Pinned project inventory with {len(selected)} indexed documents.",
        problem_scope=[],
        supported_robots=inventory.robots,
        supported_simulators=inventory.simulators,
        ros_distros=inventory.ros_distros,
        languages=inventory.languages,
        frameworks=inventory.frameworks,
        hardware_requirements=[],
        training_methods=[item for item in ("reinforcement_learning", "imitation_learning") if item.replace("_", " ") in " ".join(document.content.casefold() for document in selected)],
        deployment_modes=["container"] if inventory.has_container else [],
        licenses=[source.license] if source.license else [],
        key_components=[component.path for component in components],
        known_limitations=inventory.unknowns,
        important_issues=issue_paths,
        important_pull_requests=pull_paths,
        related_papers=[],
        wiki_root_page=root_page,
        evidence_refs=all_evidence[:50],
    )
    if store is not None:
        with store.transaction():
            for document in selected:
                store.put_document(document)
            store.put_document(facts_document)
            for evidence in all_evidence:
                store.put_evidence(evidence)
            for component in components:
                store.put_component(component)
            for page in pages:
                store.put_wiki_page(page)
            store.put_project_card(card)
    return WikiCompilationResult(
        project_card=card,
        inventory=inventory,
        components=components,
        pages=pages,
        evidence_refs=all_evidence,
        changed_paths=changed,
        rebuilt_page_types=rebuilt,
        warnings=list(steer.get("warnings", [])),
    )
