#!/usr/bin/env python3
"""Report the installed pyseekdb capabilities without opening user data.

The probe is intentionally read-only by default. ``--embedded-smoke`` opens
an isolated temporary directory, creates a throwaway collection and removes
the directory when the client closes. It never uses ROSCLAW_HOME, Memory or
Practice paths.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any


def probe(*, embedded_smoke: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "installed": False,
        "version": None,
        "client_factory": False,
        "embedded": False,
        "server": False,
        "collections": False,
        "namespaces": False,
        "fulltext": False,
        "hybrid_search": False,
        "rrf": False,
        "sparse_vector": False,
        "multi_vector": False,
        "ai_rerank": False,
        "embedded_smoke": "not_requested",
        "notes": [],
    }
    if importlib.util.find_spec("pyseekdb") is None:
        result["notes"].append("pyseekdb is not installed")
        return result

    import pyseekdb

    result.update(
        installed=True,
        version=getattr(pyseekdb, "__version__", "unknown"),
        client_factory=hasattr(pyseekdb, "Client"),
        embedded=hasattr(pyseekdb, "Client"),
        server=hasattr(pyseekdb, "RemoteServerClient"),
        collections=hasattr(pyseekdb, "Collection"),
        namespaces=hasattr(pyseekdb, "Namespace"),
        fulltext=hasattr(pyseekdb, "FulltextIndexConfig"),
        sparse_vector=hasattr(pyseekdb, "SparseVectorIndexConfig"),
    )
    try:
        from pyseekdb.client.collection import Collection

        result["hybrid_search"] = hasattr(Collection, "hybrid_search")
        result["rrf"] = result["hybrid_search"]
    except Exception as exc:  # pragma: no cover - diagnostic only
        result["notes"].append(f"collection introspection failed: {exc}")

    # pyseekdb Collections expose one embedding field. ROSClaw implements
    # multi-vector units as coordinated typed rows/collections unless a
    # probed server namespace exposes a native schema that can represent all
    # configured vectors. Do not overclaim from API presence alone.
    result["multi_vector"] = False
    result["notes"].append("multi_vector requires ROSClaw coordinated fields/collections")
    result["notes"].append("AI_RERANK requires a separately probed server SQL capability")

    if embedded_smoke:
        try:
            with tempfile.TemporaryDirectory(prefix="rosclaw-know-probe-") as tmp:
                client = pyseekdb.Client(path=str(Path(tmp) / "seekdb"), database="test")
                collection = client.get_or_create_collection("rosclaw_capability_probe")
                result["embedded_smoke"] = (
                    "ok" if collection is not None else "collection_unavailable"
                )
        except Exception as exc:  # pragma: no cover - host dependent
            result["embedded_smoke"] = "failed"
            result["notes"].append(f"embedded smoke failed: {type(exc).__name__}: {exc}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedded-smoke", action="store_true")
    args = parser.parse_args()
    report = probe(embedded_smoke=args.embedded_smoke)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["installed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
