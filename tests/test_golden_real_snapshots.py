from __future__ import annotations

import json
from pathlib import Path


def test_golden_real_snapshot_manifest_is_complete_and_safe() -> None:
    path = Path(__file__).parent / "fixtures" / "real_snapshots" / "final_acceptance_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "rosclaw.know.golden_real_snapshots.v1"
    assert len(manifest["projects"]) == 7
    assert len({project["repository"] for project in manifest["projects"]}) == 7
    assert all(len(project["commit"]) == 40 for project in manifest["projects"])
    assert all(project["critical_claims_checked"] == 10 for project in manifest["projects"])
    assert all(project["facts_evidence"]["path"] == ".rosclaw/repo_facts.json" for project in manifest["projects"])
    assert all(len(project["facts_evidence"]["content_hash"]) == 64 for project in manifest["projects"])
    assert manifest["safety"] == {
        "fixed_commit_snapshots": True,
        "repository_code_executed": False,
        "untrusted_text_treated_as_data": True,
    }
    assert manifest["paper"]["version"].startswith("2606.11092v")
    assert manifest["paper"]["passed"] is True
