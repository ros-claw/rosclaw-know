"""Restricted execution sandbox for agent-generated code."""

from __future__ import annotations

import math
import random
import signal
from typing import Any


class AgentTimeoutError(Exception):
    """Agent code exceeded the per-trial time budget."""


class AgentCodeError(Exception):
    """Agent code failed to compile or did not define the required entrypoint."""


SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "getattr": getattr,
    "hasattr": hasattr,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "math": math,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
}


def compile_agent_code(code: str, seed: int) -> dict[str, Any]:
    """Compile ``code`` into a restricted namespace.

    The namespace exposes ``math``, a seeded ``rng`` (:class:`random.Random`),
    and a small whitelist of builtins. It does **not** expose ``__import__``
    or the full builtins module.
    """
    namespace: dict[str, Any] = {
        "__builtins__": SAFE_BUILTINS,
        "math": math,
        "rng": random.Random(seed),
    }
    try:
        compiled = compile(code, "<agent_code>", "exec")
    except SyntaxError as exc:
        raise AgentCodeError(f"syntax error in agent code: {exc}") from exc
    exec(compiled, namespace)
    return namespace


def call_agent_function(
    namespace: dict[str, Any],
    entrypoint: str,
    args: tuple[Any, ...],
    timeout: float = 5.0,
) -> Any:
    """Call ``entrypoint(*args)`` from a compiled agent namespace.

    Raises :class:`AgentCodeError` if the entrypoint is missing or not callable.
    On Linux/macOS a ``SIGALRM`` timeout is enforced; on other platforms the
    timeout is ignored and the call runs to completion.
    """
    func = namespace.get(entrypoint)
    if not callable(func):
        raise AgentCodeError(f"agent code did not define a callable entrypoint '{entrypoint}'")

    def _handler(_signum: int, _frame: Any) -> None:
        raise AgentTimeoutError(f"agent code timed out after {timeout}s")

    if timeout > 0 and hasattr(signal, "SIGALRM"):
        old_handler = signal.signal(signal.SIGALRM, _handler)
        signal.alarm(int(timeout))
        try:
            return func(*args)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    return func(*args)
