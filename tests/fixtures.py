"""Synthetic SNSS records, History databases and tab fixtures."""

from __future__ import annotations

import os
import sqlite3
import struct
import time
from collections.abc import Iterable

from tab_capsule import chrome
from tab_capsule.models import Tab, Windows


def _pad4(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 4)


def p_int(v: int) -> bytes:
    return struct.pack("<i", v)


def p_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return p_int(len(b)) + _pad4(b)


def p_str16(s: str) -> bytes:
    b = s.encode("utf-16-le")
    return p_int(len(b) // 2) + _pad4(b)


def pickle(body: bytes) -> bytes:
    return struct.pack("<I", len(body)) + body


class Snss:
    def __init__(self) -> None:
        self.records: list[bytes] = []

    def cmd(self, cid: int, payload: bytes) -> Snss:
        self.records.append(struct.pack("<HB", len(payload) + 1, cid) + payload)
        return self

    def tab_window(self, wid: int, tid: int) -> Snss:
        return self.cmd(0, struct.pack("<ii", wid, tid))

    def tab_index(self, tid: int, idx: int) -> Snss:
        return self.cmd(2, struct.pack("<ii", tid, idx))

    def nav(self, tid: int, idx: int, url: str, title: str, ts_unix: float | None = None) -> Snss:
        ts = chrome.unix_to_webkit(ts_unix) if ts_unix else 0
        body = (
            p_int(tid)
            + p_int(idx)
            + p_str(url)
            + p_str16(title)
            + p_str("PAGESTATE")
            + p_int(0)
            + p_int(0)
            + p_str("")
            + p_int(0)
            + p_str(url)
            + p_int(0)
            + struct.pack("<q", ts)
            + p_int(200)
        )
        return self.cmd(6, pickle(body))

    def selected_nav(self, tid: int, idx: int) -> Snss:
        return self.cmd(7, struct.pack("<ii", tid, idx))

    def window_type(self, wid: int, t: int = 0) -> Snss:
        return self.cmd(9, struct.pack("<ii", wid, t))

    def pinned(self, tid: int, on: bool = True) -> Snss:
        return self.cmd(12, struct.pack("<i?3x", tid, on))

    def tab_closed(self, tid: int) -> Snss:
        return self.cmd(16, struct.pack("<i4xq", tid, chrome.unix_to_webkit(time.time())))

    def window_closed(self, wid: int) -> Snss:
        return self.cmd(17, struct.pack("<i4xq", wid, chrome.unix_to_webkit(time.time())))

    def last_active(self, tid: int, t: float) -> Snss:
        return self.cmd(21, struct.pack("<i4xq", tid, chrome.unix_to_webkit(t)))

    def pruned_from_back(self, tid: int, idx: int) -> Snss:
        return self.cmd(5, struct.pack("<ii", tid, idx))

    def bytes(self) -> bytes:
        return b"SNSS" + struct.pack("<i", 3) + b"".join(self.records)


def urls(windows: Windows) -> list[list[str]]:
    return [[t["url"] for t in w] for w in windows]


def make_history(path: str | os.PathLike[str], rows: Iterable[tuple[int, str, str, float]]) -> None:
    """rows: [(visit_id, url, title, visit_time_unix)] using Chrome's urls/visits schema."""
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS urls(id INTEGER PRIMARY KEY AUTOINCREMENT,url LONGVARCHAR,title LONGVARCHAR,
      visit_count INTEGER DEFAULT 0 NOT NULL,typed_count INTEGER DEFAULT 0 NOT NULL,last_visit_time INTEGER NOT NULL,
      hidden INTEGER DEFAULT 0 NOT NULL);
    CREATE TABLE IF NOT EXISTS visits(id INTEGER PRIMARY KEY AUTOINCREMENT,url INTEGER NOT NULL,visit_time INTEGER NOT NULL,
      from_visit INTEGER,external_referrer_url TEXT,transition INTEGER DEFAULT 0 NOT NULL,segment_id INTEGER,
      visit_duration INTEGER DEFAULT 0 NOT NULL);
    CREATE INDEX IF NOT EXISTS urls_url_index ON urls(url);
    """)
    for vid, url, title, t in rows:
        r = db.execute("SELECT id FROM urls WHERE url=?", (url,)).fetchone()
        uid = (
            r[0]
            if r
            else db.execute(
                "INSERT INTO urls(url,title,last_visit_time) VALUES(?,?,?)",
                (url, title, chrome.unix_to_webkit(t)),
            ).lastrowid
        )
        db.execute(
            "INSERT INTO visits(id,url,visit_time,transition,visit_duration) VALUES(?,?,?,?,?)",
            (vid, uid, chrome.unix_to_webkit(t), 0x30000001, 5),
        )
    db.commit()
    db.close()


def win(*pairs: tuple[str, str]) -> list[Tab]:
    return [
        {"url": u, "title": t, "pinned": False, "last_active": None, "nav_time": None}
        for u, t in pairs
    ]
