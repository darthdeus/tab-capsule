"""Read Chrome session/history formats and discover profiles."""

import glob
import json
import os
import shutil
import sqlite3
import struct
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from .models import Tab, Windows

# Chrome stores times as microseconds since 1601-01-01 UTC.
WEBKIT_EPOCH_OFFSET_US = 11644473600 * 1_000_000

# SNSS command ids (chromium components/sessions/core/session_service_commands.cc)
CMD_SET_TAB_WINDOW = 0
CMD_SET_TAB_INDEX_IN_WINDOW = 2
CMD_NAV_PRUNED_FROM_BACK = 5
CMD_UPDATE_TAB_NAVIGATION = 6
CMD_SET_SELECTED_NAV_INDEX = 7
CMD_SET_SELECTED_TAB_IN_INDEX = 8
CMD_SET_WINDOW_TYPE = 9
CMD_SET_PINNED_STATE = 12
CMD_TAB_CLOSED = 16
CMD_WINDOW_CLOSED = 17
CMD_LAST_ACTIVE_TIME = 21
CMD_NAV_PRUNED = 24

SKIP_URLS = {"", "about:blank", "chrome://newtab/", "chrome://new-tab-page/"}


def webkit_to_unix(us: object) -> float | None:
    """Chrome µs-since-1601 → unix seconds (float), or None if implausible (before 2000 or >1 day ahead)."""
    if not isinstance(us, int) or us <= 0:
        return None
    t = (us - WEBKIT_EPOCH_OFFSET_US) / 1_000_000
    if t < 946684800 or t > time.time() + 86400:
        return None
    return t


def unix_to_webkit(t: float) -> int:
    return int(round(t * 1_000_000)) + WEBKIT_EPOCH_OFFSET_US


def iter_snss_commands(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield (command_id, payload) from an SNSS file's bytes. Tolerates a truncated tail."""
    if len(data) < 8 or data[:4] != b"SNSS":
        raise ValueError("not an SNSS file")
    p = 8
    n = len(data)
    while p + 2 <= n:
        size = struct.unpack_from("<H", data, p)[0]
        if size == 0 or p + 2 + size > n:
            return  # end marker or partial trailing record (Chrome mid-write)
        yield data[p + 2], data[p + 3 : p + 2 + size]
        p += 2 + size


class PickleReader:
    """Reads a base::Pickle: uint32 payload size header, then 4-byte aligned fields."""

    def __init__(self, buf: bytes) -> None:
        if len(buf) < 4:
            raise ValueError("pickle too short")
        size = struct.unpack_from("<I", buf, 0)[0]
        self.buf = buf[4 : 4 + size]
        self.p = 0

    def _take(self, n: int) -> bytes:
        if self.p + n > len(self.buf):
            raise ValueError("pickle underflow")
        b = self.buf[self.p : self.p + n]
        self.p += (n + 3) & ~3
        return b

    def int32(self) -> int:
        return int(struct.unpack("<i", self._take(4))[0])

    def int64(self) -> int:
        return int(struct.unpack("<q", self._take(8))[0])

    def string(self) -> str:
        n = self.int32()
        if n < 0:
            raise ValueError("bad string length")
        return self._take(n).decode("utf-8", "replace")

    def string16(self) -> str:
        n = self.int32()
        if n < 0:
            raise ValueError("bad string16 length")
        return self._take(2 * n).decode("utf-16-le", "replace")


def parse_navigation(payload: bytes) -> tuple[int, int, str, str, float | None]:
    """UpdateTabNavigation payload → (tab_id, nav_index, url, title, timestamp_unix_or_None)."""
    r = PickleReader(payload)
    tab_id = r.int32()
    index = r.int32()
    url = r.string()
    title = r.string16()
    ts: float | None = None
    try:  # best-effort tail; the url/title above are the strict part
        r.string()  # encoded page state
        r.int32()  # transition
        r.int32()  # type mask
        r.string()  # referrer url
        r.int32()  # referrer policy
        r.string()  # original request url
        r.int32()  # is overriding user agent (bool)
        ts = webkit_to_unix(r.int64())
    except ValueError:
        pass
    return tab_id, index, url, title, ts


@dataclass(slots=True)
class _TabState:
    window: int | None = None
    index: int = 0
    navs: dict[int, tuple[str, str, float | None]] = field(default_factory=dict)
    selected: int | None = None
    pinned: bool = False
    last_active: float | None = None


@dataclass(slots=True)
class _WindowState:
    order: int
    type: int = 0
    selected: int = 0


def replay_session(data: bytes) -> Windows:
    """Replay SNSS commands → list of windows; each window = list of tab dicts in tab order.

    Tab dict: url, title, pinned, last_active (unix s or None), nav_time (unix s or None).
    Windows keep the order in which Chrome first mentioned them; closed windows/tabs and
    tabs without navigations are dropped.
    """
    tabs: dict[int, _TabState] = {}
    windows: dict[int, _WindowState] = {}
    closed_windows: set[int] = set()

    def tab(tid: int) -> _TabState:
        return tabs.setdefault(tid, _TabState())

    def window(wid: int) -> _WindowState:
        if wid not in windows:
            windows[wid] = _WindowState(order=len(windows))
        return windows[wid]

    for cid, pl in iter_snss_commands(data):
        try:
            if cid == CMD_SET_TAB_WINDOW and len(pl) >= 8:
                wid, tid = struct.unpack_from("<ii", pl)
                window(wid)
                tab(tid).window = wid
            elif cid == CMD_SET_TAB_INDEX_IN_WINDOW and len(pl) >= 8:
                tid, idx = struct.unpack_from("<ii", pl)
                tab(tid).index = idx
            elif cid == CMD_NAV_PRUNED_FROM_BACK and len(pl) >= 8:
                tid, idx = struct.unpack_from("<ii", pl)
                t = tab(tid)
                t.navs = {k: v for k, v in t.navs.items() if k < idx}
            elif cid == CMD_NAV_PRUNED and len(pl) >= 12:
                tid, idx, count = struct.unpack_from("<iii", pl)
                t = tab(tid)
                navs = {}
                for k, v in t.navs.items():
                    if k < idx:
                        navs[k] = v
                    elif k >= idx + count:
                        navs[k - count] = v
                t.navs = navs
            elif cid == CMD_UPDATE_TAB_NAVIGATION:
                tid, idx, url, title, ts = parse_navigation(pl)
                tab(tid).navs[idx] = (url, title, ts)
            elif cid == CMD_SET_SELECTED_NAV_INDEX and len(pl) >= 8:
                tid, idx = struct.unpack_from("<ii", pl)
                tab(tid).selected = idx
            elif cid == CMD_SET_SELECTED_TAB_IN_INDEX and len(pl) >= 8:
                wid, idx = struct.unpack_from("<ii", pl)
                window(wid).selected = idx
            elif cid == CMD_SET_WINDOW_TYPE and len(pl) >= 8:
                wid, wtype = struct.unpack_from("<ii", pl)
                window(wid).type = wtype
            elif cid == CMD_SET_PINNED_STATE and len(pl) >= 5:
                tid = struct.unpack_from("<i", pl)[0]
                tab(tid).pinned = pl[4] != 0
            elif cid == CMD_TAB_CLOSED and len(pl) >= 4:
                tabs.pop(struct.unpack_from("<i", pl)[0], None)
            elif cid == CMD_WINDOW_CLOSED and len(pl) >= 4:
                wid = struct.unpack_from("<i", pl)[0]
                windows.pop(wid, None)
                closed_windows.add(wid)
                for tid in [k for k, v in tabs.items() if v.window == wid]:
                    del tabs[tid]
            elif cid == CMD_LAST_ACTIVE_TIME and len(pl) >= 16:
                tid = struct.unpack_from("<i", pl)[0]
                tab(tid).last_active = webkit_to_unix(struct.unpack_from("<q", pl, 8)[0])
        except (ValueError, struct.error):
            continue  # one malformed record shouldn't sink the snapshot

    out: Windows = []
    for wid, w in sorted(windows.items(), key=lambda kv: kv[1].order):
        wtabs: list[tuple[int, int, Tab]] = []
        for tid, t in tabs.items():
            if t.window != wid or not t.navs:
                continue
            sel = t.selected
            if sel is None or sel not in t.navs:
                sel = max(t.navs)
            url, title, ts = t.navs[sel]
            if url in SKIP_URLS and not title:
                continue
            wtabs.append(
                (
                    t.index,
                    tid,
                    {
                        "url": url,
                        "title": title,
                        "pinned": t.pinned,
                        "last_active": t.last_active,
                        "nav_time": ts,
                    },
                )
            )
        wtabs.sort(key=lambda x: (x[0], x[1]))
        if wtabs:
            out.append([x[2] for x in wtabs])
    return out


def list_profiles(chrome_dir: str | os.PathLike[str]) -> list[tuple[str, str, str]]:
    """[(dir_name, display_name, path)] for profiles that have Sessions/ or History."""
    names = {}
    try:
        with open(os.path.join(chrome_dir, "Local State"), encoding="utf-8") as f:
            cache = json.load(f).get("profile", {}).get("info_cache", {})
        names = {k: v.get("name") or k for k, v in cache.items()}
    except (OSError, ValueError):
        pass
    out: list[tuple[str, str, str]] = []
    if not os.path.isdir(chrome_dir):
        return out
    for d in sorted(os.listdir(chrome_dir)):
        path = os.path.join(chrome_dir, d)
        if os.path.isdir(os.path.join(path, "Sessions")) or os.path.isfile(
            os.path.join(path, "History")
        ):
            out.append((d, names.get(d, d), path))
    return out


def newest_session_file(profile_path: str | os.PathLike[str]) -> str | None:
    files = glob.glob(os.path.join(profile_path, "Sessions", "Session_*"))
    return max(files, key=os.path.getmtime) if files else None


def import_history_file(
    db: sqlite3.Connection, profile: str, history_path: str | os.PathLike[str], retries: int = 3
) -> int:
    """Copy History(+journal) to a temp dir, open the copy, INSERT OR IGNORE visits. Returns rows added."""
    history_path = os.fspath(history_path)
    last_err: sqlite3.DatabaseError | None = None
    for attempt in range(retries):
        with tempfile.TemporaryDirectory(prefix="tab-capsule-") as tmp:
            dst = os.path.join(tmp, "History")
            shutil.copyfile(history_path, dst)
            journal = history_path + "-journal"
            if os.path.exists(journal):
                shutil.copyfile(journal, dst + "-journal")
            try:
                src = sqlite3.connect(dst)  # read-write so a hot journal gets rolled back
                rows = src.execute(
                    "SELECT v.visit_time, u.url, u.title, v.transition, v.visit_duration"
                    " FROM visits v JOIN urls u ON u.id = v.url"
                ).fetchall()
                src.close()
            except sqlite3.DatabaseError as e:  # caught Chrome mid-write: retry
                last_err = e
                time.sleep(1 + attempt)
                continue
        before = db.total_changes
        db.executemany(
            "INSERT OR IGNORE INTO visits(profile, visit_time, url, title, transition, visit_duration)"
            " VALUES(?,?,?,?,?,?)",
            [(profile,) + tuple(r) for r in rows],
        )
        db.commit()
        return db.total_changes - before
    assert last_err is not None, "at least one history-import attempt is required"
    raise last_err
