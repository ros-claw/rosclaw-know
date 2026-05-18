"""AST-based code-context enrichment.

If a wiki page embeds Python code (fenced blocks or .py snippets), extract a
compact signature summary that gets appended to the LLM input so the
extractor can spot fix patterns hiding in function names / docstrings.
"""
from __future__ import annotations

import ast
import re

_CODE_FENCE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_ast_functions(text: str, max_signatures: int = 20) -> str:
    """Return a compact string summary of Python definitions found in *text*.

    Pulls definitions from fenced ```python blocks. Each line is one of:
        def foo(a, b) — docstring summary
        class Bar
        CONST = literal
    """
    chunks = _CODE_FENCE.findall(text)
    if not chunks:
        return ""
    sigs: list[str] = []
    for chunk in chunks:
        try:
            tree = ast.parse(chunk)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                args = ", ".join(a.arg for a in node.args.args)
                doc = ast.get_docstring(node)
                summary = doc.strip().split("\n", 1)[0] if doc else ""
                line = f"def {node.name}({args})"
                if summary:
                    line += f" — {summary[:80]}"
                sigs.append(line)
            elif isinstance(node, ast.ClassDef):
                sigs.append(f"class {node.name}")
            elif isinstance(node, ast.Assign):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    if isinstance(node.value, ast.Constant):
                        sigs.append(f"{node.targets[0].id} = {node.value.value!r}")
            if len(sigs) >= max_signatures:
                break
        if len(sigs) >= max_signatures:
            break
    if not sigs:
        return ""
    return "\n\n[Python code context]\n" + "\n".join(sigs[:max_signatures])
