# Tab Capsule

A local archive of Chrome tabs and browsing history. Capture open windows and tabs,
keep visits after Chrome expires them, and browse snapshots by day in a searchable,
self-contained HTML page. Data stays on your machine; no account or server is needed.

## Requirements

Python 3.10+ with SQLite support. No third-party Python dependencies.
The default Chrome path targets Linux; pass `--chrome-dir` for another profile root.
The generated page needs a browser supporting `DecompressionStream`.

## Run

```sh
python3 tab_capsule.py --help
python3 tab_capsule.py run --chrome-dir "$HOME/.config/google-chrome" --data-dir "$HOME/.local/share/tab-capsule"
python3 tab_capsule.py stats
```

Open `~/.local/share/tab-capsule/index.html` in your browser. `snapshot`,
`import-history`, and `render` can also be run separately. `TAB_CAPSULE_DIR` overrides
the default data directory. Scheduling is optional; run the command manually or
from your own scheduler.

## Synthetic demo

```sh
python3 demo.py --output-dir /tmp/tab-capsule-demo
```

Open `/tmp/tab-capsule-demo/index.html`. The demo uses invented pages and profiles;
it never reads Chrome. Repeating the command produces the same demo dataset.

## Development

Use uv and just for development. Full checks also need Node.js 20+ and an installed
Chrome/Chromium; Python-only work needs neither.

```sh
just setup           # uv sync --locked and npm ci
just check           # lint, formatting, Python tests, compilation and browser tests
just check-python    # Python checks only; uv run syncs the Python environment
just format          # format Python sources
just demo            # synthetic page at demo-output/index.html
just cli --help      # CLI help; e.g. just cli render --data-dir /path/to/archive
just                 # list all recipes, including individual checks
```

Recipes use the locked uv environment in `.venv`. Set `UV_PYTHON` to select its
interpreter, `PYTHON` to override the Python command, or `CHROME_BIN` to select the
browser executable. `just demo` accepts an optional output directory; `just cli`
forwards all arguments to `tab_capsule.py`, including `--data-dir` and `--chrome-dir`.

Ruff configuration lives in `pyproject.toml`; `uv.lock` and `package-lock.json` pin
development tools. To update Ruff, run `uv lock --upgrade-package ruff` and rerun
the checks. Tests use synthetic data and temporary browser profiles. Browser checks
open the HTML directly and fail on external requests or JavaScript errors.

`tab_capsule.py` handles collection, SQLite, CLI and rendering. Its `build_data()`
compacts the archive and `render_html()` embeds it with the HTML/CSS/JavaScript in
`frontend/`. Keep that directory beside the Python module. Edit frontend sources,
rerun `just demo` or `just cli render`, and reload the page; no build step is needed.
The generated HTML can be moved on its own. In JavaScript, start at
`load()` → `init()` → `route()`. Python tests are in `test_tab_capsule.py`; browser
checks are in `tests/frontend.test.cjs`.

## Behavior and limitations

- Snapshot identity includes tab order, URL and pinned state, but excludes titles.
- Empty profiles are skipped. The page retains the last snapshot per profile/day.
- Search starts at two trimmed characters after a 200 ms debounce. Every
  whitespace-separated term must occur as a case-insensitive literal substring
  across URL/title. Visits are newest first (up to 300 displayed); tab matches
  deduplicate per profile/URL at the latest matching day (up to 100 displayed).
- The full archive is embedded in the HTML; large-archive performance is unmeasured.
- The parser handles a subset of Chrome's session format; other browsers are unverified.

## Privacy

Databases and generated HTML contain browsing history. Keep them out of git and
never publish them as demo data. The page loads no external favicons or scripts.
