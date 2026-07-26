#!/usr/bin/env python3
"""Check that every translation carries the same keys as ``strings.json``.

Home Assistant reads ``strings.json`` as the source of truth and each file under
``translations/`` as a rendering of it. A key that only exists in one of them fails
silently at runtime: the user sees a raw key, or a translated string that Home
Assistant never looks up. This check turns both cases into a build failure.

Run locally with::

    python scripts/check_translations.py

Only the standard library is used, so the check needs no test environment.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "holabrain"
SOURCE = COMPONENT / "strings.json"
TRANSLATIONS = COMPONENT / "translations"

# Home Assistant substitutes these at render time; dropping one in a translation
# leaves the placeholder visible to the user or breaks the format call.
PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def flatten(node: object, prefix: str = "") -> dict[str, str]:
    """Return ``{"config.step.user.title": "Sign in", ...}`` for a nested mapping."""
    if isinstance(node, dict):
        flat: dict[str, str] = {}
        for key, value in node.items():
            flat.update(flatten(value, f"{prefix}.{key}" if prefix else key))
        return flat
    return {prefix: str(node)}


def load(path: Path) -> dict[str, str]:
    try:
        return flatten(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as err:
        raise SystemExit(f"{path}: invalid JSON: {err}") from err


def check(language: Path, source: dict[str, str]) -> list[str]:
    """Return the problems found in one translation file."""
    translated = load(language)
    problems = [f"missing key: {key}" for key in sorted(source.keys() - translated.keys())]
    problems += [f"unknown key: {key}" for key in sorted(translated.keys() - source.keys())]

    for key in sorted(source.keys() & translated.keys()):
        if not translated[key].strip():
            problems.append(f"empty value: {key}")
            continue
        expected = set(PLACEHOLDER.findall(source[key]))
        actual = set(PLACEHOLDER.findall(translated[key]))
        if dropped := expected - actual:
            problems.append(f"{key}: placeholder(s) dropped: {', '.join(sorted(dropped))}")
        if unknown := actual - expected:
            problems.append(f"{key}: unknown placeholder(s): {', '.join(sorted(unknown))}")
    return problems


def main() -> int:
    if not SOURCE.is_file():
        raise SystemExit(f"{SOURCE} not found")
    source = load(SOURCE)

    languages = sorted(TRANSLATIONS.glob("*.json"))
    if not languages:
        raise SystemExit(f"no translations found in {TRANSLATIONS}")

    failed = False
    for language in languages:
        problems = check(language, source)
        if problems:
            failed = True
            print(f"{language.relative_to(COMPONENT.parent.parent)}:")
            for problem in problems:
                print(f"  {problem}")
        else:
            print(f"{language.stem}: OK ({len(source)} keys)")

    if failed:
        print("\nTranslations are out of sync with strings.json.", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
