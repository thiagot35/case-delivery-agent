"""Project rule: every function and method, including private and nested ones, has at least a one-line docstring."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = sorted(p for folder in ("src", "scripts", "tests") for p in (ROOT / folder).rglob("*.py"))


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_function_has_a_docstring(path: Path) -> None:
    """Fail listing each function in `path` that has no docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing = [
        f"{node.name} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not ast.get_docstring(node)
    ]
    assert not missing, f"functions without a docstring in {path.relative_to(ROOT)}: {', '.join(missing)}"
