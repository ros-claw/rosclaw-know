"""Operator CLI for legacy migration and offline bundle lifecycle."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
from pathlib import Path

from rosclaw_know.store import create_know_store

from .bundle import (
    HMACBundleSigner,
    build_offline_bundle,
    import_offline_bundle,
    verify_offline_bundle,
)
from .exporter import export_legacy_assets
from .importer import import_legacy_assets


def _store(args: argparse.Namespace):
    kwargs = {
        "mode": args.store_mode,
        "allow_test_memory": args.store_mode == "memory" and args.allow_test_memory,
    }
    if args.store_mode == "embedded":
        kwargs["path"] = args.store_path
    elif args.store_mode == "server":
        kwargs.update(
            host=args.seekdb_host,
            port=args.seekdb_port,
            tenant=args.seekdb_tenant,
            user=args.seekdb_user,
            password=os.environ.get("SEEKDB_PASSWORD", ""),
            database=args.know_database,
            memory_database=os.environ.get("ROSCLAW_MEMORY_DATABASE"),
            practice_database=os.environ.get("ROSCLAW_PRACTICE_DATABASE"),
        )
    return create_know_store(**kwargs)


def _signer() -> HMACBundleSigner | None:
    key = os.environ.get("ROSCLAW_KNOW_BUNDLE_HMAC_KEY")
    return HMACBundleSigner(key.encode()) if key else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rosclaw-know-assets")
    parser.add_argument(
        "--store-mode", choices=["embedded", "server", "memory"], default="embedded"
    )
    parser.add_argument("--store-path", type=Path)
    parser.add_argument("--allow-test-memory", action="store_true")
    parser.add_argument("--seekdb-host", default=os.environ.get("SEEKDB_HOST", "127.0.0.1"))
    parser.add_argument(
        "--seekdb-port", type=int, default=int(os.environ.get("SEEKDB_PORT", "2881"))
    )
    parser.add_argument("--seekdb-tenant", default=os.environ.get("SEEKDB_TENANT", "sys"))
    parser.add_argument("--seekdb-user", default=os.environ.get("SEEKDB_USER", "root"))
    parser.add_argument(
        "--know-database", default=os.environ.get("ROSCLAW_KNOW_DATABASE", "rosclaw_know")
    )
    subs = parser.add_subparsers(dest="command", required=True)
    legacy_import = subs.add_parser("import-legacy")
    legacy_import.add_argument("bridge", type=Path)
    legacy_import.add_argument("--patterns", type=Path)
    legacy_export = subs.add_parser("export-legacy")
    legacy_export.add_argument("output", type=Path)
    bundle_export = subs.add_parser("bundle-export")
    bundle_export.add_argument("output", type=Path)
    bundle_import = subs.add_parser("bundle-import")
    bundle_import.add_argument("bundle", type=Path)
    bundle_verify = subs.add_parser("bundle-verify")
    bundle_verify.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    if args.command == "bundle-verify":
        print(json.dumps(verify_offline_bundle(args.bundle, signer=_signer()), sort_keys=True))
        return 0
    store = _store(args)
    try:
        if args.command == "import-legacy":
            result = import_legacy_assets(
                store, bridge_path=args.bridge, patterns_dir=args.patterns
            )
        elif args.command == "export-legacy":
            result = export_legacy_assets(store, args.output)
        elif args.command == "bundle-export":
            result = build_offline_bundle(store, args.output, signer=_signer())
        else:
            result = import_offline_bundle(store, args.bundle, signer=_signer())
        payload = dataclasses.asdict(result)
        print(json.dumps(payload, default=str, sort_keys=True))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
