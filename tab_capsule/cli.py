"""tab-capsule: a passive time capsule of Chrome browsing.

Each run, for every Chrome profile:
  1. parses the newest SNSS session file (Sessions/Session_*) and stores a snapshot of the
     open tabs, but only when the tab set changed since the last snapshot (otherwise it
     bumps last_seen_at on that snapshot);
  2. copies History (+ History-journal) and imports visits into a permanent archive with
     INSERT OR IGNORE, so rows survive Chrome's 90-day expiry;
  3. regenerates a self-contained index.html "time capsule" browser in the data dir.

Python stdlib only. Data dir: ~/.local/share/tab-capsule (override: --data-dir or
TAB_CAPSULE_DIR). The data is private browsing history: it is never published anywhere.
"""

import argparse
import os
import sqlite3
import time
from collections.abc import Sequence

from .chrome import (
    WEBKIT_EPOCH_OFFSET_US,
    import_history_file,
    list_profiles,
    newest_session_file,
    replay_session,
)
from .models import Log
from .render import cmd_render, local_day
from .storage import open_db, set_profile_name, store_snapshot

DEFAULT_CHROME_DIR = os.path.expanduser("~/.config/google-chrome")
DEFAULT_DATA_DIR = os.path.expanduser("~/.local/share/tab-capsule")


def cmd_snapshot(
    db: sqlite3.Connection, chrome_dir: str | os.PathLike[str], log: Log = print
) -> None:
    for d, name, path in list_profiles(chrome_dir):
        set_profile_name(db, d, name)
        sf = newest_session_file(path)
        if not sf:
            continue
        with open(sf, "rb") as f:
            data = f.read()
        try:
            windows = replay_session(data)
        except ValueError as e:
            log(f"{d}: cannot parse {os.path.basename(sf)}: {e}")
            continue
        n = sum(len(w) for w in windows)
        if n == 0:
            log(f"{d}: no open tabs (profile closed), nothing to snapshot")
            continue
        _, created = store_snapshot(db, d, windows, source=os.path.basename(sf))
        log(
            f"{d}: {n} tabs in {len(windows)} windows ({'new snapshot' if created else 'unchanged'})"
        )


def cmd_import_history(
    db: sqlite3.Connection, chrome_dir: str | os.PathLike[str], log: Log = print
) -> None:
    for d, name, path in list_profiles(chrome_dir):
        set_profile_name(db, d, name)
        hp = os.path.join(path, "History")
        if not os.path.isfile(hp):
            continue
        try:
            added = import_history_file(db, d, hp)
        except (OSError, sqlite3.DatabaseError) as e:
            log(f"{d}: history import failed: {e}")
            continue
        log(f"{d}: +{added} visits")


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
    ap.add_argument("command", choices=["run", "snapshot", "import-history", "render", "stats"])
    ap.add_argument("--chrome-dir", default=DEFAULT_CHROME_DIR)
    ap.add_argument("--data-dir", default=os.environ.get("TAB_CAPSULE_DIR") or DEFAULT_DATA_DIR)
    a = ap.parse_args(argv)
    os.umask(0o077)
    db = open_db(a.data_dir)
    if a.command in ("run", "snapshot"):
        cmd_snapshot(db, a.chrome_dir)
    if a.command in ("run", "import-history"):
        cmd_import_history(db, a.chrome_dir)
    if a.command in ("run", "render"):
        cmd_render(db, a.data_dir)
    if a.command == "stats":
        cmd_stats(db)
    db.close()
    return 0
