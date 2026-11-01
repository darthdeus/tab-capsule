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

## Try it without your browsing data

```sh
python3 demo.py --output-dir /tmp/tab-capsule-demo
```

Open `/tmp/tab-capsule-demo/index.html`. The demo uses invented pages and profiles;
it never reads Chrome. Repeating the command produces the same demo dataset.

## Command shortcuts

With `just` installed, use the optional shortcuts:

```sh
just                 # list available commands
just demo            # generate demo-output/index.html using synthetic data
just setup           # install browser-test dependencies with npm ci
just check           # Python tests, compilation and browser tests
just cli --help      # show the full CLI help
just run --chrome-dir "$HOME/.config/google-chrome" --data-dir "$HOME/.local/share/tab-capsule"
just render --data-dir "$HOME/.local/share/tab-capsule"
just stats --data-dir "$HOME/.local/share/tab-capsule"
```

`just test`, `just compile` and `just test-browser` run checks separately.
`just snapshot` and `just import-history` also forward CLI options. `just demo`
accepts an optional output directory, for example `just demo /tmp/tab-capsule-demo`.
Set `PYTHON` to choose a Python executable; `TAB_CAPSULE_DIR` and `CHROME_BIN`
continue to work with the shortcuts.

## Development

Collection replays Chrome's newest SNSS session into open tabs and imports visits
from a temporary copy of History. SQLite keeps changed snapshots and deduplicated
visits. `build_data()` selects the last snapshot per profile/day and produces the
compact string table, visit rows and day ranges used by the page. `render_html()`
inlines the frontend and gzip/base64 data into one HTML file. The browser inflates
that data and handles day navigation and search locally.

Source map:

- `tab_capsule.py`: parsing, collection, database schema, `build_data()`, rendering
  and CLI. Keep `frontend/` beside this module when copying the source tool.
- `frontend/capsule.html`: page shell with data, style and script placeholders.
- `frontend/capsule.css`: page styles, including dark mode and narrow screens.
- `frontend/capsule.js`: decompression, day/snapshot rendering, literal search and
  input/hash/keyboard handlers. Start at `load()` → `init()` → `route()`.
- `demo.py`: repeatable synthetic archive generation.
- `test_tab_capsule.py`: Python fixtures and collection/rendering checks.
- `tests/frontend.test.cjs`: browser checks using only synthetic archives.
- `justfile`: shortcuts for development checks and CLI commands.

Edit the frontend files, then rerun `demo.py` or the `render` command and reload the
generated page. No frontend build step is required. The generated HTML can be moved
on its own and opened via `file://`; it needs no source files or server alongside it.

```sh
python3 -m unittest discover -v
python3 -m py_compile tab_capsule.py demo.py
```

For frontend changes, also run the browser checks. They require Node.js 20+ and an
installed Chrome/Chromium. Playwright is a development dependency only; Python
runtime dependencies are unchanged. The lockfile pins the test dependency.

```sh
npm ci
npm test
# Optional executable overrides:
CHROME_BIN=/path/to/chromium PYTHON=/path/to/python3 npm test
```

The Python tests use synthetic session files and temporary databases. The browser
checks generate the demo, move its HTML away from its original directory and open
it directly with a fresh temporary browser profile. They check loading/styles,
day navigation, search terms/order/limits, shortcuts, escaping and empty data, and
fail on external requests or JavaScript errors. No tests read your Chrome profile.

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
