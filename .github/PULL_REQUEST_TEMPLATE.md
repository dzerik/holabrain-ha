<!--
Thanks for contributing. Fill in what applies and delete what does not.
CONTRIBUTING.md has the details behind every item in the checklist.
-->

## What this changes

<!-- One paragraph: what it does and why. Link the issue it closes, e.g. "Closes #12". -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] New or corrected appliance mapping (`registry.py`)
- [ ] Refactor / internals only
- [ ] Documentation
- [ ] CI, tooling or dependencies

## How it was verified

<!--
Which tests cover it, and — for anything touching an appliance — whether it was tried on
real hardware or only against the modelled protocol. Say so plainly either way.
-->

## Checklist

- [ ] `pytest` passes locally.
- [ ] `ruff check custom_components tests scripts` is clean (this is what CI runs; do not reformat files the change does not touch).
- [ ] New behaviour is covered by a test that would fail without the change; no tests added purely for coverage.
- [ ] If `aiodollin/` was touched: `pytest tests/aiodollin/test_no_ha_imports.py` still passes (the core imports nothing from Home Assistant).
- [ ] If user-visible strings changed: `strings.json` updated **and** all five translations (`en`, `ru`, `be`, `kk`, `uz`), verified with `python scripts/check_translations.py`.
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]`, written for a user rather than for a diff.
- [ ] Version bumped in **both** `pyproject.toml` and `custom_components/holabrain/manifest.json`, following SemVer.
- [ ] Documentation updated if behaviour changed (`README.md`, `docs/`, `info.md`).
- [ ] No credentials, account e-mail addresses, real appliance serials or MAC addresses in the code, tests, fixtures or commit messages.
