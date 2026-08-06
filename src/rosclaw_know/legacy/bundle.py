"""Deterministic, hash-verified cognitive-wiki offline bundles."""

from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from rosclaw_know.store import KnowStore

from .exporter import render_legacy_assets
from .importer import LegacyImportReport, import_legacy_assets

_MAX_FILES = 10_000
_MAX_UNCOMPRESSED = 100_000_000


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class BundleSigner(Protocol):
    algorithm: str
    key_id: str

    def sign(self, payload: bytes) -> str: ...

    def verify(self, payload: bytes, signature: str) -> bool: ...


@dataclass(frozen=True)
class HMACBundleSigner:
    """Local/offline integrity signer; deployments may supply an Ed25519 signer."""

    key: bytes
    key_id: str = "local"
    algorithm: str = "hmac-sha256"

    def sign(self, payload: bytes) -> str:
        return hmac.new(self.key, payload, hashlib.sha256).hexdigest()

    def verify(self, payload: bytes, signature: str) -> bool:
        return hmac.compare_digest(self.sign(payload), signature)


@dataclass(frozen=True)
class BundleReport:
    path: Path
    bundle_sha256: str
    index_version: str
    signed: bool
    file_count: int


def build_offline_bundle(
    store: KnowStore, output_path: str | Path, *, signer: BundleSigner | None = None
) -> BundleReport:
    bridge, patterns, index_version = render_legacy_assets(store)
    files = {"assets/bridge_index.json": bridge}
    files.update({f"assets/code_patterns/{name}": data for name, data in patterns.items()})
    manifest = {
        "schema_version": "rosclaw.cognitive_wiki_bundle.v1",
        "index_version": index_version,
        "freshness": "offline_snapshot",
        "license": "unknown_unless_declared_in_evidence",
        "files": [
            {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for name, data in sorted(files.items())
        ],
        "signature": None,
    }
    if signer is not None:
        unsigned = _canonical(manifest)
        manifest["signature"] = {
            "algorithm": signer.algorithm,
            "key_id": signer.key_id,
            "value": signer.sign(unsigned),
        }
    files["manifest.json"] = _canonical(manifest) + b"\n"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    payload = output_path.read_bytes()
    return BundleReport(
        path=output_path,
        bundle_sha256=hashlib.sha256(payload).hexdigest(),
        index_version=index_version,
        signed=signer is not None,
        file_count=len(files),
    )


def verify_offline_bundle(bundle_path: str | Path, *, signer: BundleSigner | None = None) -> dict:
    bundle_path = Path(bundle_path)
    with zipfile.ZipFile(bundle_path) as archive:
        infos = archive.infolist()
        if len(infos) > _MAX_FILES:
            raise ValueError("offline bundle file-count limit exceeded")
        if sum(info.file_size for info in infos) > _MAX_UNCOMPRESSED:
            raise ValueError("offline bundle uncompressed-size limit exceeded")
        names = {info.filename for info in infos}
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("offline bundle contains an unsafe path")
        if "manifest.json" not in names:
            raise ValueError("offline bundle manifest is missing")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") != "rosclaw.cognitive_wiki_bundle.v1":
            raise ValueError("unsupported offline bundle schema")
        if not manifest.get("license") or not manifest.get("index_version"):
            raise ValueError("offline bundle lacks license or snapshot/index metadata")
        for item in manifest.get("files", []):
            name = item["path"]
            if name not in names:
                raise ValueError(f"offline bundle file missing: {name}")
            data = archive.read(name)
            if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError(f"offline bundle hash mismatch: {name}")
        signature = manifest.get("signature")
        if signature is not None:
            if signer is None:
                raise ValueError("signed offline bundle requires a matching verifier")
            unsigned = dict(manifest)
            unsigned["signature"] = None
            if (
                signature.get("algorithm") != signer.algorithm
                or signature.get("key_id") != signer.key_id
                or not signer.verify(_canonical(unsigned), signature.get("value", ""))
            ):
                raise ValueError("offline bundle signature verification failed")
        return manifest


def import_offline_bundle(
    store: KnowStore, bundle_path: str | Path, *, signer: BundleSigner | None = None
) -> LegacyImportReport:
    verify_offline_bundle(bundle_path, signer=signer)
    with tempfile.TemporaryDirectory(prefix="rosclaw-know-bundle-") as temp:
        root = Path(temp)
        with zipfile.ZipFile(bundle_path) as archive:
            for name in sorted(archive.namelist()):
                if name == "manifest.json" or name.endswith("/"):
                    continue
                target = root / PurePosixPath(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
        return import_legacy_assets(
            store,
            bridge_path=root / "assets" / "bridge_index.json",
            patterns_dir=root / "assets" / "code_patterns",
            origin="offline_bundle",
        )
