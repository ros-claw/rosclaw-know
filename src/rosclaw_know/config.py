"""Global config — loads from .env, exposes typed constants."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# Data paths
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = DATA_DIR / "assets"
CODE_PATTERNS_DIR = ASSETS_DIR / "code_patterns"
BENCHMARKS_DIR = DATA_DIR / "benchmarks"
DB_PATH = DATA_DIR / "rosclaw_knowledge.db"

# Wiki source — symlinked from the legacy rosclaw-wiki repo
WIKI_DIR = PROJECT_ROOT / os.environ.get("WIKI_DIR", "wiki")

# DeepSeek API
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_EXTRACTOR_MODEL = os.environ.get("DEEPSEEK_EXTRACTOR_MODEL", "deepseek-v4-flash")
DEEPSEEK_MUSE_MODEL = os.environ.get("DEEPSEEK_MUSE_MODEL", "deepseek-v4-pro")

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
    """Create all output directories. Idempotent."""
    for path in (DATA_DIR, ASSETS_DIR, CODE_PATTERNS_DIR, BENCHMARKS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def llm_configured() -> bool:
    """True if real LLM calls are possible."""
    return MOCK_LLM or bool(DEEPSEEK_API_KEY and DEEPSEEK_API_KEY.startswith("sk-"))
