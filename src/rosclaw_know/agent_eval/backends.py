"""Agent backends for Phase 9.

- :class:`SyntheticBackend` returns deterministic stub code so CI can exercise
  the full harness without API keys.
- :class:`LLMBackend` asks the configured DeepSeek-style model to write code.
- :class:`ClaudeBackend` is a thin optional wrapper around the Anthropic SDK.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from rosclaw_know import config
from rosclaw_know import llm as llm_module

from .synthetic_tasks import TASK_STUBS
from .types import AgentBackend, ArmName, EvalTask

log = logging.getLogger("rosclaw_know.agent_eval.backends")


IMPORT_RE = re.compile(r"^\s*(?:from\s+\S+\s+import|import\s+)")


def _extract_code(raw: str | None) -> str:
    """Pull the first fenced Python block out of an LLM response.

    Strips ``import`` lines because the sandbox already injects ``math`` and
    ``rng``; real ``import`` statements fail in the restricted namespace.
    """
    if raw is None:
        raise RuntimeError("LLM returned no content")
    # Try explicit python fence first.
    match = re.search(r"```python\n(.*?)\n```", raw, re.DOTALL)
    if match:
        code = match.group(1)
    else:
        # Fall back to any fence.
        match = re.search(r"```\n?(.*?)\n?```", raw, re.DOTALL)
        if match:
            code = match.group(1)
        else:
            # Return the whole string if it already looks like code.
            stripped = raw.strip()
            if "def " in stripped:
                code = stripped
            else:
                raise RuntimeError(f"LLM response did not contain a code block: {raw[:200]!r}")
    # Remove allowed/accidental import statements; keep the rest of the line
    # intact so line numbers reported by the sandbox remain close to original.
    cleaned = "\n".join(line for line in code.splitlines() if not IMPORT_RE.match(line))
    return cleaned.strip()


def _hint_for_arm(task: EvalTask, arm: ArmName) -> str:
    """Select the hint text to inject for a given arm."""
    if arm == "true_know":
        return task.canonical_hint
    if arm == "placebo_know":
        return task.placebo_hint
    if arm == "shuffled_know":
        return task.shuffled_hint
    if arm in ("task_pack_only", "task_pack_plus_catalyst"):
        return task.task_pack_hint
    return ""


class SyntheticBackend:
    """Deterministic code stubs for harness sanity checks."""

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def run(self, task: EvalTask, arm: ArmName, seed: int) -> str:
        stubs = TASK_STUBS.get(task.task_id, {})
        if arm in ("baseline", "placebo_know", "shuffled_know"):
            return stubs.get("baseline", stubs.get("true_know", self._noop(task)))
        return stubs.get("true_know", self._noop(task))

    @staticmethod
    def _noop(task: EvalTask) -> str:
        if task.entrypoint == "detect":
            return f"def {task.entrypoint}(log_lines):\n    return []\n"
        return f"def {task.entrypoint}(state, t, params):\n    return 0.0\n"


class LLMBackend:
    """DeepSeek / OpenAI-compatible backend using the project's ``llm`` module."""

    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4000,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def run(self, task: EvalTask, arm: ArmName, seed: int) -> str:
        hint = _hint_for_arm(task, arm)
        user = self._build_prompt(task, hint)
        system = (
            "You are a control-systems coding assistant. "
            "Output **only** the requested Python function and no explanation."
        )

        try:
            import aiohttp
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError("aiohttp is required for LLMBackend") from exc

        async def _call() -> str | None:
            async with aiohttp.ClientSession() as session:
                return await llm_module.chat(
                    session,
                    system,
                    user,
                    model=self.model or config.DEEPSEEK_MUSE_MODEL,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    timeout=180,
                )

        raw = asyncio.run(_call())
        return _extract_code(raw)

    def _build_prompt(self, task: EvalTask, hint: str) -> str:
        parts = [
            task.description,
            "",
            f"Write a single Python function named `{task.entrypoint}` with exactly the signature shown above.",
            "Use only the ``math`` module and the provided ``rng`` (a seeded ``random.Random`` instance).",
            "Do not import anything; do not write a main block or test cases.",
            "If the signature includes ``params``, read physical constants from it with ``params.get('key', default)``; do not unpack ``params``.",
        ]
        if hint:
            parts.extend(["", f"Hint: {hint}"])
        return "\n".join(parts)


class ClaudeBackend:
    """Optional Anthropic backend. Skips gracefully if ``anthropic`` is absent."""

    def __init__(
        self, model: str = "claude-sonnet-4-6", temperature: float = 0.3, max_tokens: int = 1200
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def run(self, task: EvalTask, arm: ArmName, seed: int) -> str:
        try:
            import anthropic  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError("anthropic SDK is required for ClaudeBackend") from exc

        hint = _hint_for_arm(task, arm)
        user = self._build_prompt(task, hint)
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system="You are a control-systems coding assistant. Output only the requested Python function.",
            messages=[{"role": "user", "content": user}],
        )
        content = resp.content[0].text if resp.content else ""
        return _extract_code(content)

    def _build_prompt(self, task: EvalTask, hint: str) -> str:
        parts = [task.description, "", f"Write a single Python function named `{task.entrypoint}`."]
        if hint:
            parts.extend(["", f"Hint: {hint}"])
        return "\n".join(parts)


BACKENDS: dict[str, Any] = {
    "synthetic": SyntheticBackend,
    "llm": LLMBackend,
    "claude": ClaudeBackend,
}


def build_backend(name: str, **kwargs: Any) -> AgentBackend:
    """Factory for named backends."""
    if name not in BACKENDS:
        raise ValueError(f"unknown backend {name!r}; choose from {list(BACKENDS)}")
    return BACKENDS[name](**kwargs)


__all__ = ["BACKENDS", "ClaudeBackend", "LLMBackend", "SyntheticBackend", "build_backend"]
