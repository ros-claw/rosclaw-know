#!/usr/bin/env python3
"""scripts/run_research_server.py — launch the rosclaw-know HTTP layer.

Default port 8089 (rosclaw-how lives on 8088). Reads:

  ROSCLAW_KNOW_HOST          (default: 127.0.0.1)
  ROSCLAW_KNOW_PORT          (default: 8089)
  ROSCLAW_KNOW_API_KEYS      optional, comma-separated bearer keys
  ROSCLAW_HOW_RELOAD_URL     for know→how auto-notification
                             (default: http://127.0.0.1:8088/wiki/v1/admin/reload)
  ROSCLAW_HOW_API_KEY        used to sign the reload notification
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    from rosclaw_know.api import run
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
