# Tab Capsule

Local Chrome tab/history archive. Python standard library + SQLite; generated HTML
contains vanilla JavaScript/CSS. Preserve local-only operation and the standalone
HTML output unless the user chooses otherwise.

## Structure

- `tab_capsule.py`: source-checkout CLI entry point; `python -m tab_capsule` also works.
- `tab_capsule/`: Chrome parsing/import, SQLite writes, page rendering and CLI modules.
- `frontend/capsule.{html,css,js}`: readable page sources, inlined when rendering.
- `tests/test_*.py`, `tests/fixtures.py`: grouped Python tests and synthetic fixtures.
- `tests/frontend.test.cjs`: synthetic standalone-page browser checks.
- `demo.py`: repeatable synthetic HTML or persistent seeded archive/fake Chrome profiles.
- `just seed` / `just sandbox`: isolated developer data and CLI commands; never use real profiles.
- `justfile`: development checks, synthetic demo and CLI shortcuts.
- `tab_capsule/models.py`: shared tab and generated-page type contracts.
- `pyproject.toml`, `uv.lock`: Python metadata, Ruff/mypy settings and locked dev tools.

## Required checks before committing

`python3 -m unittest discover -v` and
`python3 -m py_compile tab_capsule.py demo.py` must pass.
Use `uv sync --locked` to set up the Python development environment. Run
`just check-python` for Ruff lint/format checks, strict mypy, unittest and compilation,
or use `uv run --locked` for the individual commands. Keep development tools out of
runtime dependencies and retain direct stdlib-only Python execution. Type checking
covers `tab_capsule/` and `demo.py`; use Python 3.10-compatible annotations.
For frontend behavior changes, add and run appropriate JavaScript/browser checks:
`npm ci` then `npm test` (Node.js 20+ and Chrome/Chromium; `CHROME_BIN` can override
the executable path). The Python suite does not execute browser interactions.

Never read actual Chrome profiles for tests. Use temporary directories and synthetic
records. Never commit capsule.db, generated history pages, browser sessions or real
URLs from the user's history. Keep CLI overrides working. Avoid unrelated rewrites.
Search improvements are not implemented yet; agree on behavior before choosing an
index or fuzzy-matching algorithm. Do not publish or choose a license implicitly.
