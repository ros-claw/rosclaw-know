"""Untrusted-source normalization and path/size guards."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"execute\s+(this\s+)?(command|code|shell)", re.IGNORECASE),
    re.compile(r"developer\s+message", re.IGNORECASE),
    re.compile(r"<\s*(system|assistant|tool)\b", re.IGNORECASE),
)
BINARY_SUFFIXES = {
    ".7z",
    ".a",
    ".avi",
    ".bin",
    ".ckpt",
    ".dll",
    ".dylib",
    ".gif",
    ".gz",
    ".jpg",
    ".jpeg",
    ".mp4",
    ".onnx",
    ".pdf",
    ".png",
    ".pt",
    ".pyc",
    ".so",
    ".tar",
    ".whl",
    ".zip",
}


def safe_repo_path(value: str) -> str:
    normalized = value.replace("\\", "/").lstrip("/")
    path = PurePosixPath(normalized)
    if not normalized or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe repository path: {value!r}")
    return str(path)


def is_probably_binary(path: str, body: bytes | None = None) -> bool:
    if PurePosixPath(path).suffix.casefold() in BINARY_SUFFIXES:
        return True
    sample = (body or b"")[:1024]
    return b"\x00" in sample


def normalize_untrusted_text(text: str) -> tuple[str, list[str]]:
    """Label source text as data and report prompt-injection signals.

    Content is not interpreted as instructions and is never executed. We do
    not erase suspicious text because evidence hashes and line references
    must remain auditable; consumers receive an explicit metadata signal.
    """

    signals = [pattern.pattern for pattern in PROMPT_INJECTION_PATTERNS if pattern.search(text)]
    return text.replace("\r\n", "\n").replace("\r", "\n"), signals
