"""tab-capsule: a passive time capsule of Chrome browsing.

Each run, for every Chrome profile:
  1. parses the newest SNSS session file (Sessions/Session_*) and stores a snapshot of the
     open tabs, but only when the tab set changed since the last snapshot (otherwise it
     bumps last_seen_at on that snapshot);
  2. copies History (+ History-journal) and imports visits into a permanent archive with
     INSERT OR IGNORE, so rows survive Chrome's 90-day expiry;
  3. regenerates a self-contained index.html "time capsule" browser in the data dir.

`browse` opens the archive in an interactive terminal browser instead (curses).

Python stdlib only. Data dir: ~/.local/share/tab-capsule (override: --data-dir or
TAB_CAPSULE_DIR); Chrome dir: ~/.config/google-chrome (--chrome-dir or
TAB_CAPSULE_CHROME_DIR). The data is private browsing history: it is never published.
--sandbox uses the synthetic archive made by `python3 demo.py` instead of both.
"""

import argparse
import os
import sqlite3
import sys
import time
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from .chrome import (
    WEBKIT_EPOCH_OFFSET_US,
    import_history_file,
    list_profiles,
    newest_session_file,
    replay_session,
)
from .models import Log
from .render import build_data, cmd_render, local_day
from .storage import open_db, set_profile_name, store_snapshot

DEFAULT_CHROME_DIR = os.path.expanduser("~/.config/google-chrome")
DEFAULT_DATA_DIR = os.path.expanduser("~/.local/share/tab-capsule")
# Development sandbox created by demo.py in the source checkout: a synthetic archive
# with its own fake Chrome profiles. The marker file says it is safe to replace.
SANDBOX_DIR = Path(__file__).resolve().parent.parent / "demo-output" / "sandbox"
SANDBOX_MARKER = ".tab-capsule-demo"


def cmd_snapshot(
    db: sqlite3.Connection, chrome_dir: str | os.PathLike[str], log: Log = print
) -> bool:
    success = True
    for d, name, path in list_profiles(chrome_dir):
        set_profile_name(db, d, name)
        try:
            sf = newest_session_file(path)
            if not sf:
                continue
            with open(sf, "rb") as f:
                data = f.read()
        except OSError as error:
            log(f"{d}: snapshot failed: {error}")
            success = False
            continue
        try:
            windows = replay_session(data)
        except ValueError as e:
            log(f"{d}: cannot parse {os.path.basename(sf)}: {e}")
            success = False
            continue
        n = sum(len(w) for w in windows)
        if n == 0:
            log(f"{d}: no open tabs (profile closed), nothing to snapshot")
            continue
        _, created = store_snapshot(db, d, windows, source=os.path.basename(sf))
        log(
            f"{d}: {n} tabs in {len(windows)} windows ({'new snapshot' if created else 'unchanged'})"
        )
    return success


def cmd_import_history(
    db: sqlite3.Connection, chrome_dir: str | os.PathLike[str], log: Log = print
) -> bool:
    success = True
    for d, name, path in list_profiles(chrome_dir):
        set_profile_name(db, d, name)
        hp = os.path.join(path, "History")
        if not os.path.isfile(hp):
            continue
        try:
            added = import_history_file(db, d, hp)
        except (OSError, sqlite3.DatabaseError) as e:
            log(f"{d}: history import failed: {e}")
            success = False
            continue
        log(f"{d}: +{added} visits")
    return success


def cmd_stats(db: sqlite3.Connection, log: Log = print) -> None:
    names = dict(db.execute("SELECT dir, name FROM profiles"))
    profs = sorted(
        set(names)
        | {r[0] for r in db.execute("SELECT DISTINCT profile FROM visits")}
        | {r[0] for r in db.execute("SELECT DISTINCT profile FROM snapshots")}
    )
    for p in profs:
        n, lo, hi = db.execute(
            "SELECT COUNT(*), MIN(visit_time), MAX(visit_time) FROM visits WHERE profile=?", (p,)
        ).fetchone()
        ns = db.execute("SELECT COUNT(*) FROM snapshots WHERE profile=?", (p,)).fetchone()[0]
        last = db.execute(
            "SELECT id, taken_at, last_seen_at FROM snapshots WHERE profile=? ORDER BY taken_at DESC, id DESC LIMIT 1",
            (p,),
        ).fetchone()
        rng = ""
        if lo:
            rng = f"{local_day((lo - WEBKIT_EPOCH_OFFSET_US) / 1e6)} .. {local_day((hi - WEBKIT_EPOCH_OFFSET_US) / 1e6)}"
        line = f"{p} ({names.get(p, p)}): {n} visits {rng}; {ns} snapshots"
        if last:
            tabs, wins = db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT window_idx) FROM snapshot_tabs WHERE snapshot_id=?",
                (last[0],),
            ).fetchone()
            line += f"; latest {tabs} tabs in {wins} windows (taken {time.strftime('%Y-%m-%d %H:%M', time.localtime(last[1]))}, seen {time.strftime('%Y-%m-%d %H:%M', time.localtime(last[2]))})"
        log(line)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument(
        "command", choices=["run", "snapshot", "import-history", "render", "stats", "browse"]
    )
    ap.add_argument(
        "--chrome-dir",
        help="Chrome profiles root (default: $TAB_CAPSULE_CHROME_DIR or ~/.config/google-chrome)",
    )
    ap.add_argument(
        "--data-dir",
        help="archive directory (default: $TAB_CAPSULE_DIR or ~/.local/share/tab-capsule)",
    )
    ap.add_argument(
        "--sandbox",
        action="store_true",
        help="use the synthetic archive and fake Chrome profiles made by `python3 demo.py`"
        " (demo-output/sandbox) instead of real data",
    )
    a = ap.parse_args(argv)
    if a.sandbox:
        if a.data_dir or a.chrome_dir:
            ap.error("--sandbox sets both directories; drop --data-dir/--chrome-dir")
        if not (SANDBOX_DIR / SANDBOX_MARKER).is_file():
            ap.error(f"no sandbox at {SANDBOX_DIR}; create it with `python3 demo.py`")
        a.data_dir, a.chrome_dir = str(SANDBOX_DIR), str(SANDBOX_DIR / "chrome")
    a.data_dir = a.data_dir or os.environ.get("TAB_CAPSULE_DIR") or DEFAULT_DATA_DIR
    a.chrome_dir = a.chrome_dir or os.environ.get("TAB_CAPSULE_CHROME_DIR") or DEFAULT_CHROME_DIR
    os.umask(0o077)
    with closing(open_db(a.data_dir)) as db:
        success = True
        if a.command in ("run", "snapshot"):
            if not cmd_snapshot(db, a.chrome_dir):
                success = False
        if a.command in ("run", "import-history"):
            if not cmd_import_history(db, a.chrome_dir):
                success = False
        if a.command in ("run", "render"):
            cmd_render(db, a.data_dir)
        if a.command == "stats":
            cmd_stats(db)
        if a.command == "browse":
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                print("browse needs an interactive terminal", file=sys.stderr)
                return 2
            from .tui import run  # curses is unavailable on some platforms

            run(build_data(db))
        return 0 if success else 1
