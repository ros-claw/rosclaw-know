#!/usr/bin/env python3
"""Compatibility entrypoint for deterministic legacy asset exports."""

import argparse
import os

from rosclaw_know.legacy import export_legacy_assets
from rosclaw_know.store import create_know_store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--store-mode", choices=["embedded", "server"], default="embedded")
    parser.add_argument("--store-path")
    parser.add_argument("--seekdb-host", default=os.environ.get("SEEKDB_HOST", "127.0.0.1"))
    parser.add_argument(
        "--seekdb-port", type=int, default=int(os.environ.get("SEEKDB_PORT", "2881"))
    )
    parser.add_argument("--seekdb-tenant", default=os.environ.get("SEEKDB_TENANT", "sys"))
    parser.add_argument("--seekdb-user", default=os.environ.get("SEEKDB_USER", "root"))
    parser.add_argument(
        "--know-database", default=os.environ.get("ROSCLAW_KNOW_DATABASE", "rosclaw_know")
    )
    args = parser.parse_args()
    kwargs = {"mode": args.store_mode}
    if args.store_mode == "embedded":
        kwargs["path"] = args.store_path
    else:
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
    store = create_know_store(**kwargs)
    try:
        report = export_legacy_assets(store, args.output)
        print(report.bridge_path)
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
