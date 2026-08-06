from __future__ import annotations

import json
from pathlib import Path

import pytest

from rosclaw_know.legacy import (
    HMACBundleSigner,
    build_offline_bundle,
    export_legacy_assets,
    import_legacy_assets,
    import_offline_bundle,
    verify_offline_bundle,
)
from rosclaw_know.store import InMemoryKnowStore


def _assets(root: Path) -> tuple[Path, Path]:
    bridge = root / "bridge_index.json"
    patterns = root / "code_patterns"
    patterns.mkdir(parents=True)
    bridge.write_text(
        json.dumps(
            {
                "schema_version": "v2",
                "symptom_clusters": {
                    "camera_error": {
                        "standard_name": "Camera driver returns error -5",
                        "domain": "Perception",
                        "topic_group": "camera",
                        "associated_patterns": ["pattern_camera_error"],
                        "cross_domain_analogies": [
                            {
                                "insight": "The driver and firmware capability may differ.",
                                "action_suggestion": "Check the version compatibility table.",
                            }
                        ],
                    }
                },
            },
            sort_keys=True,
        )
    )
    (patterns / "pattern_v2_camera_error.md").write_text("# Pattern\n\nInspect version gate.\n")
    return bridge, patterns


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_legacy_import_is_idempotent_and_truthful(tmp_path):
    bridge, patterns = _assets(tmp_path)
    store = InMemoryKnowStore()
    first = import_legacy_assets(store, bridge_path=bridge, patterns_dir=patterns)
    second = import_legacy_assets(store, bridge_path=bridge, patterns_dir=patterns)
    units = list(store.iter_units())
    assert first.units_created == 1
    assert second.units_created == 0
    assert len(units) == 1
    assert units[0].provenance_status == "legacy_unknown"
    assert units[0].status == "draft"
    assert "unknown" in units[0].limitations[0].lower()
    snapshot = store.get_snapshot(first.snapshot_id)
    assert snapshot is not None and snapshot.immutable


def test_legacy_export_is_byte_deterministic(tmp_path):
    bridge, patterns = _assets(tmp_path / "input")
    store = InMemoryKnowStore()
    import_legacy_assets(store, bridge_path=bridge, patterns_dir=patterns)
    one = tmp_path / "one"
    two = tmp_path / "two"
    first = export_legacy_assets(store, one)
    second = export_legacy_assets(store, two)
    assert first.bridge_sha256 == second.bridge_sha256
    assert _tree_bytes(one) == _tree_bytes(two)


def test_signed_bundle_is_deterministic_verifiable_and_offline_marked(tmp_path):
    bridge, patterns = _assets(tmp_path / "input")
    store = InMemoryKnowStore()
    import_legacy_assets(store, bridge_path=bridge, patterns_dir=patterns)
    signer = HMACBundleSigner(b"fixture-secret", key_id="fixture")
    first = build_offline_bundle(store, tmp_path / "one.cwiki", signer=signer)
    second = build_offline_bundle(store, tmp_path / "two.cwiki", signer=signer)
    assert first.bundle_sha256 == second.bundle_sha256
    manifest = verify_offline_bundle(first.path, signer=signer)
    assert manifest["freshness"] == "offline_snapshot"
    assert manifest["signature"]["algorithm"] == "hmac-sha256"
    with pytest.raises(ValueError, match="signature"):
        verify_offline_bundle(first.path, signer=HMACBundleSigner(b"wrong", key_id="fixture"))

    target = InMemoryKnowStore()
    report = import_offline_bundle(target, first.path, signer=signer)
    assert report.origin == "offline_bundle"
    unit = next(target.iter_units())
    assert any("freshness" in item for item in unit.limitations)
