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

Open `~/.local/share/tab-capsule/index.html` in your browser, or browse the archive in
the terminal:

```sh
python3 tab_capsule.py browse
```

`browse` shows the days on the left and, for the selected day, each profile's open tabs
(carried forward from the last snapshot) followed by that day's history, newest first.
Keys: `↑↓`/`jk` move, `←→`/`hl`/`Tab` switch between days and entries, `[` `]` older
or newer day, `PgUp`/`PgDn`, `g`/`G` first/last, `Enter` shows the full URL, `?` help,
`Esc`/`q` quit. It uses the standard library's curses and needs an interactive
terminal. It has no search yet; the HTML page does. `snapshot`,
`import-history`, and `render` can also be run separately. `TAB_CAPSULE_DIR` overrides
the default data directory. Scheduling is optional; run the command manually or
from your own scheduler.
Collection continues across profiles and returns a nonzero exit status if any
profile fails; `run` still renders the successfully collected data.

## Synthetic demo

```sh
python3 demo.py --output-dir /tmp/tab-capsule-demo
```

Open `/tmp/tab-capsule-demo/index.html`. The demo uses invented pages and profiles;
it never reads Chrome. Repeating the command produces the same demo dataset.

For a persistent development workspace, seed a new directory:

```sh
just seed                         # 2 profiles × 30 days × 100 visits = 6,000 visits
just sandbox stats
just sandbox render
just sandbox run                  # collect from the generated fake Chrome profiles
```

Open `demo-output/sandbox/index.html`. The directory also contains `capsule.db` and
`chrome/` with synthetic History databases and session files. `just sandbox` pins
both CLI paths to this workspace, regardless of your normal data-directory setting.
All of these generated files are ignored by Git. `just seed` replaces
`demo-output/sandbox` each time, since it only ever holds seeded data. `demo.py --keep-db`
itself refuses an existing output directory; choose a new one for another dataset:

```sh
just seed --output-dir /tmp/tab-capsule-large --days 90 --visits-per-day 1000 --profiles 3
just cli stats --data-dir /tmp/tab-capsule-large
# Without just:
python3 demo.py --keep-db --corpus realistic --output-dir /tmp/tab-capsule-dev --days 30
```

`just seed` uses `--corpus realistic` (`tests/corpus.py`): thousands of invented pages
on familiar kinds of sites, a few favourites visited many times a day, a long tail of
searches and one-off pages, tabs that drift across windows, and short words that often
occur inside longer ones. It approximates the shape of a real archive without any real
history. The default `--corpus small` is the fixed four-page demo used by the browser
tests. Without just: `python3 demo.py --keep-db --corpus realistic --output-dir DIR`.

Data is repeatable for the same options and local timezone. The seeder reuses the
synthetic Chrome builders in `tests/fixtures.py`; it needs no installed browser.

## Development

### Checks (no installation needed)

The whole test suite needs only Python 3.10+ and an installed Chrome or Chromium: no
packages, no Node.js and no network access.

```sh
python3 -m unittest discover -v
python3 -m compileall -q tab_capsule tests tab_capsule.py demo.py
```

`tests/test_browser.py` opens generated pages in headless Chrome and drives them with
trusted keyboard and mouse input. It fails if no browser is found: set `CHROME_BIN` to
the executable, or `TAB_CAPSULE_SKIP_BROWSER=1` to skip only those tests.

### Optional tools

[uv](https://docs.astral.sh/uv/) and [just](https://just.systems/) add Ruff lint and
format checks and strict mypy type checks.

```sh
just setup           # uv sync --locked: install Ruff and mypy into .venv
just check           # lint, formatting, type checks, all tests and compilation
just check-offline   # the two commands above, without uv
just test-python     # tests without a browser
just test-browser    # only the headless-Chrome checks
just format          # format Python sources
just demo            # synthetic page at demo-output/index.html
just cli --help      # CLI help; e.g. just cli render --data-dir /path/to/archive
just                 # list all recipes
```

Recipes use the locked uv environment in `.venv`. Set `UV_PYTHON` to select its
interpreter, `PYTHON` to override the Python command, or `CHROME_BIN` to select the
browser executable. `just demo` accepts an optional output directory; `just cli`
forwards all arguments to `tab_capsule.py`, including `--data-dir` and `--chrome-dir`.

Ruff and mypy configuration live in `pyproject.toml`; `uv.lock` pins them. Shared tab and page contracts live in `tab_capsule/models.py`;
parser state uses private dataclasses. To update Ruff, run `uv lock --upgrade-package ruff` and rerun
the checks. Tests use synthetic data and temporary browser profiles. Browser checks
open the HTML directly and fail on external requests or JavaScript errors. They talk
to Chrome over the DevTools protocol on a pipe (`--remote-debugging-pipe`), implemented
with the standard library in `tests/cdp.py`.

`tab_capsule.py` launches the `tab_capsule/` package; `python3 -m tab_capsule` works
too. The package has five modules:

- `chrome.py`: session parsing, profile discovery and History import.
- `storage.py`: SQLite schema, profiles and snapshot writes.
- `render.py`: archive reads, compact page data and self-contained HTML assembly.
- `cli.py`: command parsing, collection orchestration and statistics.
- `tui.py`: the `browse` terminal browser. `Archive` derives day views from the page
  data, `Browser` holds state, keys and `render()` (styled text lines, testable without
  a terminal); `run()` is a thin curses loop around them.

Keep `tab_capsule.py`, `tab_capsule/` and `frontend/` together when copying the
source tool. Edit frontend sources, rerun `just demo` or `just cli render`, and
reload the page; no build step is needed. The generated HTML can be moved on its
own. In JavaScript, start at `load()` → `init()` → `route()`. Python tests live in
`tests/test_*.py`, with shared synthetic builders in `tests/fixtures.py`; browser
checks are in `tests/test_browser.py`.

## Behavior and limitations

- Snapshot identity includes tab order, URL and pinned state, but excludes titles.
- Empty profiles are skipped. The page retains the last snapshot per profile/day.
- Days use the generating machine's local timezone. Search links retain those
  recorded day groups in other browser timezones; clock times use the browser's timezone.
- Search starts at two trimmed characters after a 200 ms debounce. Every
  whitespace-separated term must occur as a case-insensitive literal substring
  across URL/title. Visits are newest first (up to 300 displayed); tab matches
  deduplicate per profile/URL at the latest matching day (up to 100 displayed).
- The full archive is embedded in the HTML; large-archive performance is unmeasured.
- The parser handles a subset of Chrome's session format; other browsers are unverified.
- History capture retries if the database or its journals change during copying.
  This is a best-effort live capture, not a filesystem snapshot; persistent changes
  or unreadable files cause a reported failure without importing that capture.

## Privacy

Databases and generated HTML contain browsing history. Keep them out of git and
never publish them as demo data. The page loads no external favicons or scripts.

## License

Copyright (c) 2026 Jakub Arnold.

Licensed under either the [MIT License](LICENSE-MIT) or the
[Apache License, Version 2.0](LICENSE-APACHE), at your option.
