"""Global config — loads from .env, exposes typed constants."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def _user_data_dir(name: str) -> Path:
    """Cross-platform writable user data directory fallback."""
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    return base / name


# Directory that contains the ``rosclaw_know`` package.
# In a wheel this is ``site-packages/rosclaw_know``; in an editable install it
# is ``repo/src/rosclaw_know``.
_PACKAGE_DIR = Path(__file__).resolve().parents[0]


def _resolve_bundled_data_dir() -> Path:
    """Locate bundled data (assets + curated_registry).

    Wheel: data is bundled inside the package (``rosclaw_know/data``).
    Editable/legacy: data lives at the repository root.
    """
    wheel_data = _PACKAGE_DIR / "data"
    if (wheel_data / "assets").is_dir():
        return wheel_data
    # Editable install: repo root is one level above the package directory.
    return _PACKAGE_DIR.parents[1] / "data"


BUNDLED_DATA_DIR = _resolve_bundled_data_dir()

# Runtime-generated files (databases, caches, logs) go to a writable user dir.
# The override is intentionally resolved before directory creation so tests,
# containers, and offline bundles can keep all writes inside an isolated root.
RUNTIME_DATA_DIR = Path(
    os.environ.get("ROSCLAW_KNOW_DATA_DIR", str(_user_data_dir("rosclaw_know")))
).expanduser()
RUNTIME_DATA_DIR.mkdir(parents=True, exist_ok=True)

# For backwards compatibility: PROJECT_ROOT points at the repository root in
# editable installs and at the package parent directory in wheels.
PROJECT_ROOT = BUNDLED_DATA_DIR.parent

# Best-effort .env loading (dev editable installs). In wheels no .env exists.
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    load_dotenv(_env_file, override=False)

# Data paths
DATA_DIR = RUNTIME_DATA_DIR
ASSETS_DIR = BUNDLED_DATA_DIR / "assets"
CODE_PATTERNS_DIR = ASSETS_DIR / "code_patterns"
CURATED_REGISTRY_DIR = BUNDLED_DATA_DIR / "curated_registry"
BENCHMARKS_DIR = DATA_DIR / "benchmarks"
DB_PATH = DATA_DIR / "rosclaw_knowledge.db"

# Wiki source — symlinked from the legacy rosclaw-wiki repo
WIKI_DIR = Path(os.environ.get("WIKI_DIR", RUNTIME_DATA_DIR / "wiki"))

# DeepSeek API
#
# Defaults are the battle-tested production choice (`deepseek-chat`).
# History: prior releases shipped `deepseek-v4-flash` / `deepseek-v4-pro`
# as defaults — these are *reasoning* models that return the answer in
# `reasoning_content` and leave the public `content` field empty, which
# silently produced 0 extractions when the env was not overridden.
# Sprint 0 of the v1.5 plan fixed this: defaults now point at the
# non-reasoning chat model directly. If you actually want the reasoning
# tier, override DEEPSEEK_*_MODEL in .env explicitly.
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_EXTRACTOR_MODEL = os.environ.get("DEEPSEEK_EXTRACTOR_MODEL", "deepseek-chat")
DEEPSEEK_MUSE_MODEL = os.environ.get("DEEPSEEK_MUSE_MODEL", "deepseek-chat")

# Concurrency
HARVESTER_CONCURRENCY = int(os.environ.get("HARVESTER_CONCURRENCY", "5"))

# Embeddings
EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
)

# Mock LLM (skip real network calls — for plumbing tests)
MOCK_LLM = os.environ.get("ROSCLAW_KNOW_MOCK_LLM", "0") == "1"

# SeekDB (optional)
SEEKDB_HOST = os.environ.get("SEEKDB_HOST", "")
SEEKDB_PORT = os.environ.get("SEEKDB_PORT", "")
SEEKDB_AVAILABLE = bool(SEEKDB_HOST and SEEKDB_PORT)


def ensure_dirs() -> None:
    """Create runtime output directories. Idempotent."""
    for path in (DATA_DIR, BENCHMARKS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def llm_configured() -> bool:
    """True if real LLM calls are possible."""
    return MOCK_LLM or bool(DEEPSEEK_API_KEY and DEEPSEEK_API_KEY.startswith("sk-"))
