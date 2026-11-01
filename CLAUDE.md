# Tab Capsule

Local Chrome tab/history archive. Python standard library + SQLite; generated HTML
contains vanilla JavaScript/CSS. Preserve local-only operation and the standalone
HTML output unless the user chooses otherwise.

## Structure

- `tab_capsule.py`: SNSS parser, collection, SQLite storage, CLI, HTML assembly.
- `frontend/capsule.{html,css,js}`: readable page sources, inlined when rendering.
- `test_tab_capsule.py`: synthetic fixtures and collection/render-data tests.
- `tests/frontend.test.cjs`: synthetic standalone-page browser checks.
- `demo.py`: deterministic synthetic demonstration; never reads actual browser data.
- `justfile`: development checks, synthetic demo and CLI shortcuts.
- `pyproject.toml`, `uv.lock`: Python metadata, Ruff settings and locked dev tools.

## Required checks before committing

`python3 -m unittest discover -v` and
`python3 -m py_compile tab_capsule.py demo.py` must pass.
Use `uv sync --locked` to set up the Python development environment. Run
`just check-python` for Ruff lint/format checks, unittest and compilation, or use
`uv run --locked` for the individual commands. Keep Ruff as a development-only
dependency and retain direct stdlib-only Python execution.
For frontend behavior changes, add and run appropriate JavaScript/browser checks:
`npm ci` then `npm test` (Node.js 20+ and Chrome/Chromium; `CHROME_BIN` can override
the executable path). The Python suite does not execute browser interactions.

Never read actual Chrome profiles for tests. Use temporary directories and synthetic
records. Never commit capsule.db, generated history pages, browser sessions or real
URLs from the user's history. Keep CLI overrides working. Avoid unrelated rewrites.
Search improvements are not implemented yet; agree on behavior before choosing an
index or fuzzy-matching algorithm. Do not publish or choose a license implicitly.
