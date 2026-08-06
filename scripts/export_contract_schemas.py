#!/usr/bin/env python3
"""Export public Know/How JSON Schemas deterministically."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rosclaw_know.contracts import PUBLIC_CONTRACTS, export_contract_schemas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("schemas/know_how_v2.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = export_contract_schemas(PUBLIC_CONTRACTS)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
