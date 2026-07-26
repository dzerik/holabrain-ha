"""Compliance: the aiodollin core must stay free of Home Assistant (and framework) imports.

This is what keeps the core extractable into its own PyPI package. If any module under
``aiodollin`` imports one of the forbidden roots, this test names the file and line so it
can be fixed by widening an interface (dependency injection), never by importing HA.
"""

import ast
import pathlib

FORBIDDEN = {"homeassistant", "voluptuous", "aiohttp"}

_AIODOLLIN = (
    pathlib.Path(__file__).resolve().parents[2]
    / "custom_components"
    / "holabrain"
    / "aiodollin"
)


def _imported_roots(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        return [node.module.split(".")[0]]
    return []


def test_aiodollin_has_no_forbidden_imports():
    offenders: list[str] = []
    for path in sorted(_AIODOLLIN.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            bad = set(_imported_roots(node)) & FORBIDDEN
            if bad:
                offenders.append(f"{path.relative_to(_AIODOLLIN)}:{node.lineno} -> {sorted(bad)}")
    assert not offenders, "aiodollin must not import " + ", ".join(sorted(FORBIDDEN)) + (
        "\n  " + "\n  ".join(offenders)
    )
