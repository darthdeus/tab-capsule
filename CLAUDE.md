# Tab Capsule

Local Chrome tab/history archive. Python standard library + SQLite; generated HTML
contains vanilla JavaScript/CSS. Preserve local-only operation and the standalone
HTML output unless the user chooses otherwise.

## Structure

- `tab_capsule.py`: source-checkout CLI entry point; `python -m tab_capsule` also works.
- `tab_capsule/`: Chrome parsing/import, SQLite writes, page rendering and CLI modules.
- `tab_capsule/tui.py`: `browse` curses terminal browser. Keep state and `render()`
  free of curses; `tests/test_tui.py` tests them directly plus a pty smoke test.
- `tests/corpus.py`: realistic invented browsing for `demo.py --corpus realistic`.
- `frontend/capsule.{html,css,js}`: readable page sources, inlined when rendering.
- `tests/test_*.py`, `tests/fixtures.py`: grouped Python tests and synthetic fixtures.
- `tests/test_browser.py`: headless-Chrome page checks; `tests/cdp.py` is the stdlib
  DevTools-protocol driver (pipe transport, no packages).
- `demo.py`: with no options rebuilds `demo-output/sandbox` (realistic archive + fake
  Chrome); `tab_capsule.py --sandbox <cmd>` uses it. `--page-only` writes just the HTML.
- `just seed` / `just sandbox`: aliases for the above; never use real profiles.
- `justfile`: development checks, synthetic demo and CLI shortcuts.
- `tab_capsule/models.py`: shared tab and generated-page type contracts.
- `pyproject.toml`, `uv.lock`: Python metadata, Ruff/mypy settings and locked dev tools.

## Required checks before committing

`python3 -m unittest discover -v` (includes the browser checks; needs Chrome/Chromium,
`CHROME_BIN` overrides the path) and
`python3 -m compileall -q tab_capsule tests tab_capsule.py demo.py` must pass.
Tests must keep running with only python3 and Chrome: no third-party packages, no
Node.js, no network. Extend `tests/cdp.py` rather than adding a browser library.
`just check` adds Ruff lint/format and strict mypy (`uv sync --locked` first). Keep
development tools out of runtime dependencies. Type checking covers `tab_capsule/` and
`demo.py`; use Python 3.10-compatible annotations. Frontend behavior changes need
browser checks in `tests/test_browser.py`.

Never read actual Chrome profiles for tests. Use temporary directories and synthetic
records. Never commit capsule.db, generated history pages, browser sessions or real
URLs from the user's history. Keep CLI overrides working. Avoid unrelated rewrites.
Search improvements are not implemented yet; agree on behavior before choosing an
index or fuzzy-matching algorithm. Do not publish or choose a license implicitly.
