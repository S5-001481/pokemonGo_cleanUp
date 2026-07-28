# Contributing to pokemonGo_cleanUp

Thank you for helping improve the project.

## Scope

Changes should preserve the project's local-first and privacy-conscious design.
Do not add:

- gameplay automation;
- account, credential, or private API access;
- network traffic interception;
- copyrighted Pokémon images, icons, or screenshots;
- captured device data, secrets, or personal information.

OCR is intentionally outside the initial scope. Discuss a design before adding it.

## Development setup

Use Python 3.12 or newer and a repository-local `.venv`.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Before submitting a change, run:

```bash
pytest
ruff check .
mypy
```

Add tests for behavior changes. Keep ADB tests isolated from physical devices by
mocking the subprocess boundary.

## Privacy review

Before committing, run `git status` and inspect the diff. Never commit anything
under `data/screenshots/`, environment files, credentials, real serial numbers, or
screenshots from a device.

By contributing, you agree that your contribution is licensed under Apache-2.0.
