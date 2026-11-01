"""SQLite schema and archive writes."""

import hashlib
import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles(dir TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS snapshots(
  id INTEGER PRIMARY KEY, profile TEXT NOT NULL, taken_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL, fingerprint TEXT NOT NULL, source TEXT);
CREATE INDEX IF NOT EXISTS snapshots_profile ON snapshots(profile, taken_at);
CREATE TABLE IF NOT EXISTS snapshot_tabs(
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id), window_idx INTEGER NOT NULL,
  tab_idx INTEGER NOT NULL, url TEXT NOT NULL, title TEXT, pinned INTEGER NOT NULL DEFAULT 0,
  last_active INTEGER, nav_time INTEGER, PRIMARY KEY(snapshot_id, window_idx, tab_idx));
CREATE TABLE IF NOT EXISTS visits(
  profile TEXT NOT NULL, visit_time INTEGER NOT NULL, url TEXT NOT NULL, title TEXT,
  transition INTEGER, visit_duration INTEGER, PRIMARY KEY(profile, visit_time, url));
"""


def open_db(data_dir):
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    db = sqlite3.connect(os.path.join(data_dir, "capsule.db"))
    db.executescript(SCHEMA)
    return db


def fingerprint(windows):
    """Tab-set identity: window/tab position, url, pinned. Titles are excluded on purpose
    (unread counts like "(3) Inbox" would otherwise make a new snapshot every hour)."""
    key = [
        [w, i, t["url"], bool(t["pinned"])]
        for w, tabs in enumerate(windows)
        for i, t in enumerate(tabs)
    ]
    return hashlib.sha1(json.dumps(key, ensure_ascii=False).encode()).hexdigest()


def store_snapshot(db, profile, windows, now=None, source=None):
    """Insert a snapshot if the tab set changed; else bump last_seen_at. Returns (snapshot_id, created)."""
    now = int(now if now is not None else time.time())
    fp = fingerprint(windows)
    row = db.execute(
        "SELECT id, fingerprint FROM snapshots WHERE profile=? ORDER BY taken_at DESC, id DESC LIMIT 1",
        (profile,),
    ).fetchone()
    if row and row[1] == fp:
        db.execute("UPDATE snapshots SET last_seen_at=? WHERE id=?", (now, row[0]))
        db.commit()
        return row[0], False
    cur = db.execute(
        "INSERT INTO snapshots(profile, taken_at, last_seen_at, fingerprint, source) VALUES(?,?,?,?,?)",
        (profile, now, now, fp, source),
    )
    sid = cur.lastrowid
    db.executemany(
        "INSERT INTO snapshot_tabs(snapshot_id, window_idx, tab_idx, url, title, pinned, last_active, nav_time)"
        " VALUES(?,?,?,?,?,?,?,?)",
        [
            (
                sid,
                w,
                i,
                t["url"],
                t["title"],
                int(bool(t["pinned"])),
                int(t["last_active"]) if t.get("last_active") else None,
                int(t["nav_time"]) if t.get("nav_time") else None,
            )
            for w, tabs in enumerate(windows)
            for i, t in enumerate(tabs)
        ],
    )
    db.commit()
    return sid, True


def set_profile_name(db, d, name):
    db.execute(
        "INSERT INTO profiles(dir, name) VALUES(?,?) ON CONFLICT(dir) DO UPDATE SET name=excluded.name",
        (d, name),
    )
    db.commit()
